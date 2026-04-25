# Continuation notes for the next session

This repo was bootstrapped from `/MyWork/Projects/turing-llm/code/workbench/agent-runner/` on **2026-04-25** during a session that designed and shipped the workbench-side AgentRunner. Everything in `src/aicraft/` is a renamed copy of `wb_agent` — same architecture, same decisions. Tests are new and ATIF fixtures are real captures.

## What's done

- ✅ Source ported from `wb_agent` → `aicraft` (package + console script + env vars renamed).
- ✅ `pyproject.toml` set up for PyPI publish under `kashifpk`. License: MIT. Console script: `aicraft = "aicraft.cli:app"`.
- ✅ `LICENSE`, `.gitignore`, `README.md` written for standalone use (no workbench-specific framing).
- ✅ Tests under `tests/`:
  - `test_mount.py` — allowlist behavior including symlink resolution, no-roots edge case
  - `test_runner.py` — ATIF extraction across **real** v1.2 (claude-code) and v1.5 (codex) trajectories, plus malformed/missing/empty cases
  - `test_cli.py` — mount-spec parsing, provider preset application, error paths
  - `test_harbor_patches.py` — codex `OPENAI_BASE_URL` patch idempotency + env gating
- ✅ Real ATIF trajectory fixtures captured from a working environment, copied into `tests/fixtures/`.
- ✅ GitHub Actions CI workflow (`.github/workflows/ci.yml`) — ruff + pytest on Python 3.12.
- ✅ Initial git commit (see `git log`).
- ✅ `.venv/` created via `uv venv` and package installed editable with `[dev]` extras.
- ✅ Smoke tests passing locally — see `git log` for verification.

## What's next

In rough priority order:

1. **Push to GitHub.** `git remote add origin git@github.com:kashifpk/aicraft.git` (after creating an empty `kashifpk/aicraft` repo on GitHub) then `git push -u origin main`. The user is authenticated on `gh` as `kashifpk`, so `gh repo create kashifpk/aicraft --public --source=. --remote=origin --push` should also work.
2. **Set up PyPI trusted publishing.** Create the project on PyPI manually (one-time), then add a publish workflow. The simplest path is GitHub's official trusted-publishing flow — `pypa/gh-action-pypi-publish@release/v1` triggered on tag pushes. No API token in repo. Docs: https://docs.pypi.org/trusted-publishers/
3. **First release: tag `v0.1.0`.** Once a green CI run is on `main`, tag and let the publish workflow ship to PyPI. Smoke-test: `pip install aicraft` in a fresh venv → `aicraft list-agents`.
4. **Switch workbench's dependency.** In `/MyWork/Projects/turing-llm/code/workbench/agent-runner/pyproject.toml`, replace the embedded source with:
   ```toml
   dependencies = ["aicraft>=0.1,<0.2"]
   ```
   ...and delete `agent-runner/src/wb_agent/`. The workbench shim at `backend/app/cli/agent.py` continues to exec `aicraft` (path resolution stays the same — just the binary name changed from `wb-agent` to `aicraft`). Update the candidate paths in `_locate_wb_agent_bin()`. Also update `WB_AGENT_*` env-var references to `AICRAFT_*` for consistency.
5. **Cross-platform CI matrix.** Add macOS + Windows runners to CI eventually. Most of the package is platform-agnostic, but the mount/symlink test exercises POSIX paths and the CLI banner uses unicode box-drawing characters. Worth verifying.
6. **Tests that need Docker.** None of the current tests invoke Harbor end-to-end. An optional integration test job (gated behind `HARBOR_INTEGRATION=1`) running the `nop` agent would catch regressions in the Harbor wrapper layer. Don't run it in normal CI; gate it for nightly or manual.

## Key design decisions and why

These are worth remembering — re-litigating them is a waste of time:

- **In-process Harbor monkey-patch** for `Codex.CLI_FLAGS`, not a `.pth` shim. The shim approach was needed in workbench's perfbench plugin because perfbench shells out to `harbor` as a subprocess. aicraft uses Harbor as an in-process library, so a plain Python monkey-patch in `_harbor_patches.py` is enough.
- **Mount allowlist defaults to empty (no mounts allowed)**, not wildcard. Set `AICRAFT_MOUNT_ROOTS=...` to opt in. Fail-closed.
- **Trajectories default to `./trajectories/`** (cwd-relative), not `~/.local/share`. Stays with the project, easy to gitignore. Override with `AICRAFT_TRAJECTORY_DIR` or `--trajectory-dir`.
- **`final_text` extracted from ATIF only.** Per-agent extractors (e.g., parsing `claude-code.txt` for stream-json) were considered and rejected — agents change formats; ATIF is the standardized one and Harbor ensures it's emitted by every modern installed agent (`SUPPORTS_ATIF=True`). Returns `""` for non-ATIF agents; trajectory dir has the full record.
- **Codex pre-validates `model` is set.** Harbor reports "Model name is required" only after the ~50s agent install completes. Our `_MODEL_REQUIRED_AGENTS = {"codex"}` in `runner.py` catches this in <1s. List grows as we discover other strict agents.
- **Verifier is always disabled** (`TrialVerifierConfig(disable=True)`). aicraft is "run agent, get answer." Verification belongs to harbor-rewardkit or a higher layer.
- **Provider presets only cover OpenAI-protocol gateways.** Anthropic-protocol routing through aggregators (e.g., Claude Code via OpenRouter) is more complex (Anthropic CLI auth quirks) and deferred until there's demand.
- **`litellm>=1.83.0` floor is non-negotiable.** Versions 1.82.7 / 1.82.8 were trojanized on PyPI for ~40 minutes on 2026-03-24. The pin and its comment in `pyproject.toml` are intentional.

## Known caveats inherited from Harbor 0.4

- **Rootless Podman + claude-code DNS quirk.** Harbor's `exec_as_agent` step inside the agent install loses DNS in rootless Podman, manifesting as a misleading `Could not resolve host: claude.ai`. Docker works. Not aicraft's bug; document if anyone hits it.
- **Agent install repeats every trial** — Harbor doesn't have an "is the agent CLI already on PATH" check, so the ~50s claude-code or codex install runs on every trial in a fresh container. Image build is cached, container is fresh. We accepted this and moved on.
- **LiteLLM imports eagerly during Harbor startup**, adding ~25s even for `nop` runs. Filed as upstream issue: https://github.com/harbor-framework/harbor/issues/1514
- **ATIF schema varies per agent**: codex emits ATIF-v1.5 (rich, with `tool_calls`/`observation`/`model_name` on steps), claude-code on harbor 0.4 emits ATIF-v1.2 (minimal — just `step_id, timestamp, source, message`). Our `_extract_final_text` only uses fields present in v1.2, so it works for both. Anyone adding richer parsing (tool-call counts, edit-classification) needs to dispatch on `schema_version` or treat v1.5 fields as optional.

## Useful commands during dev

```bash
# Set up venv (one time)
uv venv
. .venv/bin/activate
uv pip install -e ".[dev]"

# Lint + test
ruff check src tests
pytest -q

# Run the CLI in editable mode
aicraft list-agents
aicraft run -a claude-code "Hello, what's 2+2?" --timeout 60

# Run a real Harbor-backed test (needs docker daemon + API keys)
DOCKER_HOST="unix://$XDG_RUNTIME_DIR/podman/podman.sock" \
  ANTHROPIC_API_KEY=... \
  aicraft run -a claude-code "..." --timeout 60
```

## Connection back to workbench

aicraft was extracted from `turing-tooling/turing-workbench`, where it lives as `agent-runner/src/wb_agent/` on the `feature/agent-runner` branch (PR open at the time of extraction). After the first `aicraft` release ships to PyPI, the workbench branch will be updated to depend on the published package and delete the embedded copy. Until then, workbench's local copy and this repo can drift; treat this repo as the canonical source.

The workbench shim at `backend/app/cli/agent.py` will continue to bridge provider env vars from `backend/.env` and exec the `aicraft` binary — that part stays workbench-specific.
