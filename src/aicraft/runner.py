"""AgentRunner — synthesize a minimal Harbor task and run it.

The CLI invokes this in-process. Higher-level callers (worker queues,
HTTP services) can also import ``AgentRunner`` directly — the API takes
structured inputs and returns structured outputs, so it's serialization-
friendly across process boundaries.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from textwrap import dedent
from typing import Any

from aicraft.mount import load_allowed_roots, validate_mounts
from aicraft.types import AgentConfig, AgentResult

logger = logging.getLogger(__name__)


# Agents that fail with ``ValueError: Model name is required`` when no
# ``model`` is set. Harbor only surfaces this AFTER the ~50s agent install
# completes, so we pre-validate to fail fast in <1 second instead. Add new
# entries as we discover them; conservative by design (false negatives are
# tolerable, false positives would block valid usage).
_MODEL_REQUIRED_AGENTS = frozenset({"codex"})


class MissingRequiredModelError(ValueError):
    """Raised when an agent that needs ``model`` was invoked without one."""


# Environment Dockerfile used for the synthesized task. Kept deliberately
# minimal — it's the agent's working environment, not our code. Most coding
# agents expect git + a POSIX shell + the language of the task. We ship a
# generic Python base + common tools.
_DEFAULT_DOCKERFILE = dedent("""\
    FROM python:3.12-slim

    RUN apt-get update \\
        && apt-get install -y --no-install-recommends git curl ca-certificates bash \\
        && rm -rf /var/lib/apt/lists/*

    WORKDIR /workspace
""")


def _resolve_trajectory_dir() -> Path:
    """Where captured trajectories live.

    Resolution order:
      1. ``AICRAFT_TRAJECTORY_DIR`` env var (for systemd / persistent setups).
      2. Default ``./trajectories`` relative to the current working directory
         — stays with the project you're running from, easy to discover,
         easy to ``.gitignore``.

    The CLI's ``--trajectory-dir`` flag short-circuits both by passing an
    explicit ``trajectory_dir`` to ``AgentRunner``.
    """
    env = os.environ.get("AICRAFT_TRAJECTORY_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd() / "trajectories"


class AgentRunner:
    """Run one coding-agent trial via Harbor and return a structured result."""

    def __init__(
        self,
        *,
        allowed_mount_roots: list[Path] | None = None,
        trajectory_dir: Path | None = None,
    ) -> None:
        # Default to env-loaded values so the class is ready to use without
        # ceremony. Tests and worker callers can inject their own.
        self._allowed_roots = (
            allowed_mount_roots
            if allowed_mount_roots is not None
            else load_allowed_roots()
        )
        self._trajectory_dir = trajectory_dir or _resolve_trajectory_dir()
        self._trajectory_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, config: AgentConfig) -> AgentResult:
        """Execute one agent trial. Blocks until the trial finishes or times out."""
        if config.agent in _MODEL_REQUIRED_AGENTS and not config.model:
            raise MissingRequiredModelError(
                f"agent {config.agent!r} requires an explicit model. "
                f"Pass model= (e.g., model='gpt-5')."
            )
        validate_mounts(config.mounts, self._allowed_roots)

        run_id = f"aicraft-{uuid.uuid4().hex[:12]}"
        logger.info("Starting agent run %s (agent=%s model=%s)", run_id, config.agent, config.model)

        # Synthesize a minimal task dir. We could keep a permanent scratch
        # location, but a throwaway temp dir is simpler — trajectories are
        # preserved under trajectory_dir, and that's the only output we care
        # about keeping.
        with _temp_task_dir(prompt=config.prompt, memory_mb=config.memory_mb) as task_dir:
            trial_config = _build_trial_config(
                task_dir=task_dir,
                trials_dir=self._trajectory_dir,
                trial_name=run_id,
                config=config,
            )

            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    _run_trial(trial_config),
                    timeout=config.timeout_s + 60,  # +60s grace for setup/teardown
                )
                # Harbor returns a TrialResult even when the trial failed —
                # the failure surfaces as `exception_info`. We have to map
                # that to our status ourselves; don't conflate "Harbor
                # returned" with "agent succeeded".
                if result.exception_info is not None:
                    return AgentResult(
                        status="error",
                        trajectory_path=self._trajectory_dir / run_id,
                        duration_s=time.monotonic() - started,
                        trial_id=run_id,
                        error=(
                            f"{result.exception_info.exception_type}: "
                            f"{result.exception_info.exception_message}"
                        ),
                    )
                return AgentResult(
                    status="completed",
                    final_text=_extract_final_text(result, self._trajectory_dir / run_id),
                    trajectory_path=self._trajectory_dir / run_id,
                    duration_s=time.monotonic() - started,
                    trial_id=run_id,
                )
            except TimeoutError:
                return AgentResult(
                    status="timeout",
                    trajectory_path=self._trajectory_dir / run_id,
                    duration_s=time.monotonic() - started,
                    trial_id=run_id,
                    error=f"Agent run exceeded {config.timeout_s}s wall-clock limit",
                )
            except Exception as e:  # noqa: BLE001 — surface anything Harbor raises
                logger.exception("Agent run %s failed", run_id)
                return AgentResult(
                    status="error",
                    trajectory_path=self._trajectory_dir / run_id
                    if (self._trajectory_dir / run_id).exists()
                    else None,
                    duration_s=time.monotonic() - started,
                    trial_id=run_id,
                    error=f"{type(e).__name__}: {e}",
                )


# ---- helpers below this point; kept module-level so they're easy to test -----


class _temp_task_dir:
    """Context manager that creates a Harbor-compatible minimal task directory."""

    def __init__(self, *, prompt: str, memory_mb: int) -> None:
        self._prompt = prompt
        self._memory_mb = memory_mb
        self._path: Path | None = None

    def __enter__(self) -> Path:
        import tempfile

        base = Path(tempfile.mkdtemp(prefix="aicraft-task-"))
        (base / "environment").mkdir()
        (base / "environment" / "Dockerfile").write_text(_DEFAULT_DOCKERFILE)
        (base / "instruction.md").write_text(self._prompt.strip() + "\n")
        (base / "task.toml").write_text(dedent(f"""\
            version = "1.0"

            [metadata]

            [verifier]
            timeout_sec = 60.0

            [agent]
            timeout_sec = 900.0

            [environment]
            build_timeout_sec = 600.0
            cpus = 1
            memory_mb = {self._memory_mb}
            storage_mb = 10240
        """))
        self._path = base
        return base

    def __exit__(self, *_: Any) -> None:
        if self._path is not None and self._path.exists():
            shutil.rmtree(self._path, ignore_errors=True)


def _build_trial_config(
    *,
    task_dir: Path,
    trials_dir: Path,
    trial_name: str,
    config: AgentConfig,
):
    """Translate our AgentConfig into Harbor's TrialConfig."""
    # Deferred import — harbor is a heavy dep and we don't want it to load
    # when someone just imports aicraft to inspect types.
    #
    # Harbor namespaces two overlapping sets: the *task-level* AgentConfig /
    # EnvironmentConfig / VerifierConfig / TaskConfig (fields inside
    # task.toml) and the *trial-level* versions used by TrialConfig. The
    # trial-level ones are exported with a Trial- prefix. We want those.
    from harbor import (
        EnvironmentType,
        TrialAgentConfig,
        TrialConfig,
        TrialEnvironmentConfig,
        TrialTaskConfig,
        TrialVerifierConfig,
    )

    mounts_json = [
        {
            "type": "bind",
            "source": str(m.host.resolve()),
            "target": str(m.container),
            **({"read_only": True} if m.read_only else {}),
        }
        for m in config.mounts
    ]

    return TrialConfig(
        task=TrialTaskConfig(path=task_dir),
        trial_name=trial_name,
        trials_dir=trials_dir,
        agent=TrialAgentConfig(
            name=config.agent,
            model_name=config.model,
            override_timeout_sec=float(config.timeout_s),
            env=config.env,
        ),
        environment=TrialEnvironmentConfig(
            type=EnvironmentType.DOCKER,
            mounts_json=mounts_json or None,
        ),
        # The verifier is disabled — aicraft is pure "run the agent,
        # capture output". Verification/rewards belong to harbor-rewardkit
        # or to a higher-level layer that wraps this runner.
        verifier=TrialVerifierConfig(disable=True),
    )


async def _run_trial(trial_config):
    """Drive Harbor's Trial lifecycle to completion and return the result."""
    from harbor import Trial

    trial = await Trial.create(trial_config)
    return await trial.run()


def _extract_final_text(_trial_result, trajectory_dir: Path) -> str:
    """Pull the agent's final textual reply from the ATIF trajectory.

    ATIF (``trajectory.json``) is Harbor's agent-agnostic schema: every step
    has a ``source`` field, and the last step authored by ``source="agent"``
    is the reply. Works for any agent with ``SUPPORTS_ATIF=True`` (most
    modern installed agents — claude-code, codex, aider, etc.).

    Returns an empty string if the trajectory is missing, malformed, or the
    agent never produced a reply (e.g., nop, or a failure mid-run). Callers
    should not treat ``""`` as semantically meaningful — read the
    trajectory directory for the full record.
    """
    import json

    atif_path = trajectory_dir / "agent" / "trajectory.json"
    if not atif_path.exists():
        return ""
    try:
        data = json.loads(atif_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    steps = data.get("steps")
    if not isinstance(steps, list):
        return ""

    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        if step.get("source") != "agent":
            continue
        msg = step.get("message")
        if isinstance(msg, str):
            return msg
    return ""
