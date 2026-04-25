# aicraft

Run coding agents (Claude Code, Codex, Aider, OpenHands, …) against prompts in container sandboxes. A thin Python library + CLI on top of the [Harbor](https://github.com/harbor-framework/harbor) framework, focused on ad-hoc single-prompt runs rather than full benchmark evaluation.

> **Status: alpha.** API and CLI may shift between 0.1.x releases. Pin minor versions if you depend on it.

## Why this exists

Harbor itself is built around full benchmark lifecycles: scaffolding a task directory, running an agent over a dataset, scoring with a verifier, publishing trajectories. That's heavy when all you want is "run agent X on prompt Y, give me the answer."

aicraft flips the abstraction:

- Inline prompt or `--prompt-file` instead of a hand-built task directory
- Verifier disabled by default
- Auto-synthesizes the minimal Harbor task structure under the hood
- ATIF-aware final-text extraction (works across any agent that emits ATIF)
- Mount allowlist enforcement (refuses to mount paths outside configured roots)
- Provider presets (`--provider openrouter` etc.) for routing OpenAI-protocol agents through aggregator gateways
- Workarounds for current Harbor 0.4 quirks (Codex `OPENAI_BASE_URL` propagation)

It's the opinionated, ad-hoc-friendly subset of Harbor — like `gh` is to a `git push origin <branch> && curl github.com/...` chain.

## Install

```bash
pip install aicraft
```

aicraft requires Python 3.12+ and a working Docker daemon (or Podman with the Docker compatibility socket — set `DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock`). Harbor itself spawns each agent in a Docker container.

## Usage

### Library

```python
import asyncio
from pathlib import Path
from aicraft import AgentRunner, AgentConfig, MountSpec

async def main():
    runner = AgentRunner()
    result = await runner.run(AgentConfig(
        prompt="Summarize the structure of /workspace/code in one paragraph.",
        agent="claude-code",
        mounts=[MountSpec(host=Path("/data/repo"), container=Path("/workspace/code"))],
        timeout_s=600,
    ))
    print(result.status)         # "completed" | "timeout" | "error"
    print(result.final_text)     # agent's textual reply (extracted from ATIF)
    print(result.trajectory_path)  # full trajectory on disk

asyncio.run(main())
```

### CLI

```bash
# Inline prompt
aicraft run -a claude-code "Find dead code in this repo"

# Prompt from a file
aicraft run -a codex -M gpt-5 -f ./prompt.md

# With a mounted code directory
AICRAFT_MOUNT_ROOTS=/data/repos aicraft run -a claude-code \
  "Refactor the parser for readability" \
  --mount /data/repos/foo:/workspace/code:ro

# Specify model and a higher timeout, write structured result to file
aicraft run -a claude-code "..." \
  --model claude-sonnet-4-7 \
  --timeout 1800 \
  --output ./result.json

# Route an OpenAI-protocol agent through OpenRouter for open-weight models
OPENROUTER_API_KEY=sk-or-... aicraft run \
  -a codex -M deepseek/deepseek-chat --provider openrouter \
  "Describe the bug in /workspace/buggy.py and fix it" \
  --mount /tmp/scratch:/workspace:rw

# See installed agents
aicraft list-agents

# Locate a previously-run trajectory on disk
aicraft trajectory aicraft-3a2b1c0d9e8f
```

Mount syntax: `host:container[:ro|rw]` — default is `ro`. Pass `:rw` explicitly when the agent needs to write back.

### Output

`aicraft run` prints a JSON document to **stdout** for pipeline use:

```json
{
  "status": "completed",
  "final_text": "def reverse_string(s: str) -> str:\n    return s[::-1]",
  "trajectory_path": "/path/to/trajectories/aicraft-7dad38f46662",
  "duration_s": 65.1,
  "trial_id": "aicraft-7dad38f46662",
  "error": null
}
```

…and a human-readable banner to **stderr** so the agent's reply is easy to spot amid debug logs:

```
════════════════════════════════════════════════════════════════════════
 AGENT OUTPUT  [OK]  trial=aicraft-7dad38f46662  65.1s
════════════════════════════════════════════════════════════════════════
def reverse_string(s: str) -> str:
    return s[::-1]
════════════════════════════════════════════════════════════════════════
```

Exit codes: `0` on `completed`, `1` on `timeout` / `error`, `2` on a pre-flight config issue (mount not allowed, missing required model, unknown provider).

The **trajectory directory** contains everything Harbor captured: the ATIF `trajectory.json`, the agent's raw session logs, and any verifier artifacts. For an aicraft run there's no verifier output, but the ATIF trajectory has the full tool-call history.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `AICRAFT_MOUNT_ROOTS` | Colon-separated absolute host paths permitted as mount sources. Empty means no mounts allowed. | *empty* |
| `AICRAFT_TRAJECTORY_DIR` | Where captured trajectories are stored. Override per-call with `--trajectory-dir`. | `./trajectories/` (cwd) |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` | Standard provider env vars consumed by Harbor agents. | — |
| `OPENROUTER_API_KEY`, `FIREWORKS_API_KEY`, `TOGETHER_API_KEY`, `GROQ_API_KEY` | Provider-specific keys consumed by `--provider <name>`. | — |

### Provider presets

`--provider <name>` rewrites `OPENAI_BASE_URL` and `OPENAI_API_KEY` from a friendly name plus a provider-specific env var, so OpenAI-protocol agents (codex, copilot-cli) can route through aggregator gateways without manual env juggling. Anthropic-protocol agents (claude-code) read `ANTHROPIC_*` directly and need separate setup.

| Preset | Base URL | Key env var |
|---|---|---|
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `fireworks` | `https://api.fireworks.ai/inference/v1` | `FIREWORKS_API_KEY` |
| `together` | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |

## Limitations

See [GOTCHAS.md](./GOTCHAS.md) for the full list with reproduction steps and workarounds. Highlights:

- **First run per agent is slow** — Harbor 0.4 re-installs the agent CLI in a fresh container on every trial (~45–55s for claude-code/codex). Image build is cached; container is fresh.
- **Rootless Podman + claude-code** — the agent runs fine, but trajectory ingestion fails reading session JSONL files (claude-code writes them at `0600`, and rootless Podman's userns mapping makes them unreadable from the host). Use Docker for claude-code, or other agents (codex, aider, nop) on Podman — they're unaffected.
- **`final_text` for non-ATIF agents** — may be empty; the trajectory directory still has the full record.
- **Codex requires an explicit `--model`** — Harbor's codex agent has no default. aicraft pre-validates this in <1s.
- **`--provider` is OpenAI-protocol only** — claude-code (Anthropic protocol) needs `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` set manually for gateways like OpenRouter.

## Related issues filed upstream

- [harbor#1514](https://github.com/harbor-framework/harbor/issues/1514) — Defer litellm/aiohttp imports to avoid ~25s startup overhead

## License

MIT — see [LICENSE](./LICENSE).
