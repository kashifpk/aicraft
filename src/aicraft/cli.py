"""aicraft CLI — a thin shell around AgentRunner.

Usage:

    aicraft run -a claude-code "Analyze this repo for dead code"
    aicraft run -a codex -f prompt.md --mount /data/repo:/workspace/code:ro
    aicraft run -a codex -M deepseek/deepseek-chat --provider openrouter "..."
    aicraft list-agents
    aicraft trajectory <run-id>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import cyclopts

from aicraft.mount import MountNotAllowedError
from aicraft.runner import AgentRunner, MissingRequiredModelError, _resolve_trajectory_dir
from aicraft.types import AgentConfig, MountSpec

app = cyclopts.App(name="aicraft", help="Run coding agents against prompts via Harbor.")


# Friendly provider name -> {OpenAI-compatible base URL, env var holding the
# provider's API key}. Each preset rewires OPENAI_BASE_URL and copies the
# provider-specific key into OPENAI_API_KEY before the trial runs, so Harbor
# agents that talk OpenAI's protocol (codex, copilot-cli) route to the right
# gateway without us having to know which agent we're invoking.
#
# Anthropic-protocol agents (claude-code) are out of scope for this map —
# they read ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL on their own.
_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_source": "OPENROUTER_API_KEY",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "key_source": "FIREWORKS_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "key_source": "TOGETHER_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_source": "GROQ_API_KEY",
    },
}


class _PresetConfigError(ValueError):
    """User-fixable provider preset misconfiguration (unknown name, missing key)."""


def _apply_provider_preset(name: str) -> None:
    """Map a friendly provider name to OPENAI_BASE_URL + OPENAI_API_KEY.

    Resolving order: the preset's ``base_url`` becomes ``OPENAI_BASE_URL``;
    the provider-specific key (``OPENROUTER_API_KEY`` etc.) is copied into
    ``OPENAI_API_KEY`` so Harbor agents that read only the OpenAI names see
    the right credentials.

    Raises ``_PresetConfigError`` if the provider isn't known or the
    expected key isn't in the environment — both are user-fixable config
    issues we'd rather surface upfront than mid-trial. The ``run`` handler
    catches it, prints a clean message, and exits 2.
    """
    if name not in _PROVIDER_PRESETS:
        known = ", ".join(sorted(_PROVIDER_PRESETS))
        raise _PresetConfigError(f"unknown provider {name!r}. Known: {known}")
    preset = _PROVIDER_PRESETS[name]
    src_key = preset["key_source"]
    src_value = os.environ.get(src_key)
    if not src_value:
        raise _PresetConfigError(
            f"provider {name!r} requires {src_key} in the environment"
        )
    os.environ["OPENAI_BASE_URL"] = preset["base_url"]
    os.environ["OPENAI_API_KEY"] = src_value


def _parse_mount(spec: str) -> MountSpec:
    """Parse ``host:container[:ro|rw]`` — Docker-ish syntax.

    Default is read-only; callers who need write access must pass ``:rw``
    explicitly. This keeps the common case safe and makes the less-safe
    case visible at the call site.
    """
    parts = spec.split(":")
    if len(parts) < 2 or len(parts) > 3:
        raise ValueError(
            f"Invalid mount spec {spec!r}. Expected host:container[:ro|rw]"
        )
    host, container = parts[0], parts[1]
    mode = parts[2] if len(parts) == 3 else "ro"
    if mode not in ("ro", "rw"):
        raise ValueError(f"Mount mode must be 'ro' or 'rw', got {mode!r}")
    return MountSpec(host=Path(host), container=Path(container), read_only=mode == "ro")


@app.command
def run(
    prompt: Annotated[
        str | None,
        cyclopts.Parameter(help="Prompt text. Omit and use --prompt-file for long prompts."),
    ] = None,
    *,
    prompt_file: Annotated[
        Path | None,
        cyclopts.Parameter(name=["--prompt-file", "-f"], help="Read the prompt from a file."),
    ] = None,
    agent: Annotated[
        str, cyclopts.Parameter(name=["--agent", "-a"], help="Harbor agent name.")
    ] = "claude-code",
    model: Annotated[
        str | None,
        cyclopts.Parameter(name=["--model", "-M"], help="Model name passed to the agent."),
    ] = None,
    mount: Annotated[
        list[str],
        cyclopts.Parameter(
            name=["--mount", "-m"],
            help="Bind mount host:container[:ro|rw]. Can be repeated. Default mode is ro.",
        ),
    ] = [],
    timeout: Annotated[
        int, cyclopts.Parameter(name=["--timeout", "-t"], help="Agent wall-clock timeout in seconds.")
    ] = 600,
    provider: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--provider", "-p"],
            help=(
                "Route OpenAI-protocol agents through a friendly provider preset "
                "(openrouter, fireworks, together, groq). Sets OPENAI_BASE_URL and "
                "copies the provider's API key (e.g. OPENROUTER_API_KEY) into "
                "OPENAI_API_KEY. No-op for agents that don't speak OpenAI protocol."
            ),
        ),
    ] = None,
    env: Annotated[
        list[str],
        cyclopts.Parameter(name=["--env", "-e"], help="KEY=VALUE env var passed to the agent. Repeatable."),
    ] = [],
    output: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--output", "-o"],
            help="Write structured JSON result to this path. Default prints to stdout.",
        ),
    ] = None,
    trajectory_dir: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--trajectory-dir"],
            help=(
                "Where to store the captured trajectory. "
                "Overrides AICRAFT_TRAJECTORY_DIR and the default ./trajectories/."
            ),
        ),
    ] = None,
    verbose: Annotated[
        bool, cyclopts.Parameter(name=["--verbose", "-v"], help="Enable debug logging to stderr.")
    ] = False,
) -> None:
    """Run one agent invocation and print/save the structured result."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if prompt is None and prompt_file is None:
        raise cyclopts.ValidationError("Provide an inline prompt OR --prompt-file.")
    if prompt is not None and prompt_file is not None:
        raise cyclopts.ValidationError("Pass either an inline prompt OR --prompt-file, not both.")
    prompt_text = prompt if prompt is not None else prompt_file.read_text(encoding="utf-8")

    if provider is not None:
        try:
            _apply_provider_preset(provider)
        except _PresetConfigError as e:
            print(f"provider preset error: {e}", file=sys.stderr)
            sys.exit(2)

    env_dict: dict[str, str] = {}
    for e in env:
        if "=" not in e:
            raise cyclopts.ValidationError(f"--env value must be KEY=VALUE, got {e!r}")
        k, v = e.split("=", 1)
        env_dict[k] = v

    try:
        mounts = [_parse_mount(m) for m in mount]
    except ValueError as e:
        raise cyclopts.ValidationError(str(e)) from e

    cfg = AgentConfig(
        prompt=prompt_text,
        agent=agent,
        model=model,
        mounts=mounts,
        env=env_dict,
        timeout_s=timeout,
    )

    runner = AgentRunner(trajectory_dir=trajectory_dir)
    try:
        result = asyncio.run(runner.run(cfg))
    except MountNotAllowedError as e:
        # Print clean error to stderr; don't dump a traceback for a config issue.
        print(f"mount not allowed: {e}", file=sys.stderr)
        sys.exit(2)
    except MissingRequiredModelError as e:
        print(f"missing required argument: {e}", file=sys.stderr)
        sys.exit(2)

    # Banner on stderr (so piping stdout to jq stays clean). Placed here,
    # after the run completes, so it lands at the visual tail of the
    # terminal output — even when --verbose is on and debug lines
    # precede it, the banner is the last thing the user sees.
    _print_result_banner(result)

    payload = result.model_dump(mode="json")
    text = json.dumps(payload, indent=2, default=str)
    if output is not None:
        output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote result to {output}", file=sys.stderr)
    else:
        print(text)

    # Non-zero exit on timeout/error so shell pipelines can react.
    if result.status != "completed":
        sys.exit(1)


def _print_result_banner(result) -> None:
    """Print the agent's final reply + run metadata prominently on stderr."""
    bar = "═" * 72
    # Map status to a short visual marker. Plain ASCII to stay safe across
    # terminals; no ANSI color since the output often gets captured into
    # logs / redirected and escape codes there are noise.
    status_tag = {
        "completed": "[OK]",
        "error": "[ERROR]",
        "timeout": "[TIMEOUT]",
    }.get(result.status, f"[{result.status.upper()}]")

    body: str
    if result.status == "completed" and result.final_text:
        body = result.final_text.strip()
    elif result.status == "completed":
        body = "(no textual reply captured — trajectory has the full record)"
    else:
        body = result.error or "(no error message)"

    print(bar, file=sys.stderr)
    print(f" AGENT OUTPUT  {status_tag}  trial={result.trial_id}  {result.duration_s:.1f}s", file=sys.stderr)
    print(bar, file=sys.stderr)
    print(body, file=sys.stderr)
    print(bar, file=sys.stderr)
    if result.trajectory_path is not None:
        print(f" trajectory: {result.trajectory_path}", file=sys.stderr)
        print(bar, file=sys.stderr)


@app.command(name="list-agents")
def list_agents() -> None:
    """List the canonical Harbor agent names accepted by ``--agent``.

    Source: ``harbor.models.agent.name.AgentName`` — not filenames, because
    several installed agents use module names that differ from their public
    CLI names (e.g., ``qwen_code.py`` → ``qwen-coder``).
    """
    from harbor.models.agent.name import AgentName

    for a in sorted(AgentName, key=lambda x: x.value):
        print(a.value)


@app.command
def trajectory(
    run_id: Annotated[str, cyclopts.Parameter(help="The trial_id returned by `aicraft run`.")],
    *,
    trajectory_dir: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--trajectory-dir"],
            help="Search this directory instead of the default location.",
        ),
    ] = None,
) -> None:
    """Print the on-disk path of a captured trajectory."""
    base = trajectory_dir or _resolve_trajectory_dir()
    path = base / run_id
    if not path.exists():
        print(f"no trajectory found for {run_id} under {base}", file=sys.stderr)
        sys.exit(1)
    print(path)


if __name__ == "__main__":
    app()
