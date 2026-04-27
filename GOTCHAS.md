# Gotchas

Operational quirks worth knowing before you debug. Most of these are inherited from the underlying Harbor framework — aicraft documents and works around them where it can.

## Container runtimes

### Rootless Podman + claude-code: trajectory ingestion fails with `Permission denied`

**Symptom.** A claude-code run completes successfully (`status: "completed"`, agent reply present), but the post-run step logs:

```
Failed to convert Claude Code events to trajectory: [Errno 13] Permission denied: \
.../trajectories/<id>/agent/sessions/projects/-workspace/<uuid>.jsonl
```

**Cause.** claude-code writes session JSONL files at mode `0600` for privacy. Under rootless Podman, the container root is mapped to a host *subuid* (e.g. `100999`), so your host user (UID 1000) cannot read those files. The agent itself runs fine; only trajectory parsing breaks.

**Workarounds.**
- Use Docker for claude-code (containers run as your UID via the socket model, so `0600` stays readable on the host).
- Or pursue `--userns=keep-id` — Harbor doesn't expose this knob today, so it would need an upstream fix or a monkey-patch.
- Other agents (codex, aider, nop) write `0644` and are unaffected on rootless Podman.

Verified 2026-04-25. This replaces an earlier rumored "DNS resolution" failure that no longer reproduces against current Harbor + Podman.

### Harbor needs a Docker-API socket

Harbor talks to a Docker-API-compatible socket via `DOCKER_HOST`. For Podman, expose its socket and point at it:

```sh
DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock aicraft run ...
```

`sudo podman <cmd>` works without a daemon for one-off CLI use, but aicraft (via Harbor) needs the socket service running.

## Authentication

### claude-code requires `ANTHROPIC_API_KEY` (or alternative)

claude-code runs in a fresh container and doesn't inherit your local Anthropic CLI auth. Set one of, in priority order:

- `ANTHROPIC_API_KEY` (preferred)
- `ANTHROPIC_AUTH_TOKEN`
- `CLAUDE_CODE_OAUTH_TOKEN`

If none are set, the run fails fast: the trajectory shows `apiKeySource: "none"` and the agent exits 1.

### `--provider` only covers OpenAI-protocol agents

`--provider openrouter|fireworks|together|groq` rewrites `OPENAI_BASE_URL` + `OPENAI_API_KEY` from the corresponding `*_API_KEY` env var, so OpenAI-protocol agents (codex, copilot-cli, aider) route through the gateway. It does **not** touch `ANTHROPIC_*`, so it's a no-op for claude-code.

To run claude-code through OpenRouter (which exposes an Anthropic-compatible endpoint), set the Anthropic vars manually:

```sh
ANTHROPIC_BASE_URL=https://openrouter.ai/api/v1 \
ANTHROPIC_API_KEY=$OPENROUTER_API_KEY \
aicraft run -a claude-code -M anthropic/claude-sonnet-4.5 "..."
```

Non-Anthropic models via Anthropic protocol on OpenRouter (e.g. `moonshotai/kimi-k2.6` with claude-code) may lose tool-use fidelity per OpenRouter's docs. For non-Anthropic models, prefer `codex --provider openrouter` instead.

## Model name handling

### Harbor's codex agent strips the vendor prefix

`harbor/agents/installed/codex.py` runs `model_name.split("/")[-1]` before invoking the codex CLI:

| You pass | Harbor sends to codex |
|---|---|
| `moonshotai/kimi-k2.6` | `kimi-k2.6` |
| `deepseek/deepseek-v4-pro` | `deepseek-v4-pro` |
| `openai/gpt-5.5-pro` | `gpt-5.5-pro` |

OpenRouter accepts short forms when unambiguous, so most runs succeed. If you route to a gateway that *requires* the namespace, this breaks silently. Track upstream; not currently patched in aicraft.

## Performance

### Per-trial agent install (~45–55 s)

Harbor has no "is the agent CLI already on PATH" check — it re-installs the agent into a fresh container on every trial. Image build is cached; container is fresh. Accepted as inherent cost for now.

### Cold start (~25 s) even for `nop`

LiteLLM imports eagerly during Harbor startup. Filed upstream: [harbor#1514](https://github.com/harbor-framework/harbor/issues/1514).

## Trajectories

### Default location is `./trajectories/` (cwd-relative)

Easy to gitignore (already in our default `.gitignore`). Override with `AICRAFT_TRAJECTORY_DIR` or `--trajectory-dir`.

### `final_text` may be empty for non-ATIF agents

aicraft extracts the agent's textual reply from the standardized ATIF trajectory only. For agents without ATIF (`SUPPORTS_ATIF=False`), `final_text` is `""` — read the raw session log under the trajectory directory instead.

### ATIF schema varies per agent

codex emits ATIF v1.5 (rich — has `tool_calls`, `observation`, `model_name`); claude-code emits ATIF v1.2 (`step_id`, `timestamp`, `source`, `message` only). aicraft's `_extract_final_text` uses only v1.2 fields, so it works for both. Anyone parsing tool calls or edit classifications needs to dispatch on `schema_version`.

## Mounts

### Mount allowlist defaults to empty (fail-closed)

Without `AICRAFT_MOUNT_ROOTS` set, `--mount` is rejected. Opt in explicitly:

```sh
AICRAFT_MOUNT_ROOTS=/data/repos:/tmp/scratch \
  aicraft run --mount /data/repos/foo:/workspace/code:ro -a claude-code "..."
```

The allowlist is checked after symlink resolution, so symlinking around it doesn't bypass the check.
