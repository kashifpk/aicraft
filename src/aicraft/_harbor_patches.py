"""Workarounds for Harbor 0.4 quirks we hit during integration.

Each patch is idempotent and prefers no-op fallbacks over hard failures so a
broken or upgraded Harbor never blocks the runner from at least starting.
Remove individual patches as the corresponding upstream fixes land.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _ensure_codex_openai_base_url_flag() -> None:
    """Forward ``OPENAI_BASE_URL`` to codex as ``-c openai_base_url="..."``.

    Harbor's codex agent class reads several env vars but doesn't propagate
    ``OPENAI_BASE_URL`` to the codex CLI — codex itself only honors the value
    via ``-c openai_base_url=...`` overrides or ``config.toml``, not via env.
    The result: when the user has ``OPENAI_BASE_URL`` pointing at a gateway
    (Turing GW, OpenRouter, regional OpenAI endpoints, etc.) and an API key
    bound to that gateway, codex still hits ``api.openai.com`` and gets
    ``401 incorrect_hostname``.

    The fix is to register an extra ``CliFlag`` on ``Codex`` that maps an
    ``openai_base_url`` kwarg (with ``env_fallback="OPENAI_BASE_URL"``) to
    the right ``-c`` invocation. Once registered, Harbor's existing kwarg
    plumbing fills it in from the env.

    Idempotent. No-op if ``OPENAI_BASE_URL`` is unset or Harbor's codex
    module can't be imported.
    """
    if not os.environ.get("OPENAI_BASE_URL"):
        return
    try:
        from harbor.agents.installed.base import CliFlag
        from harbor.agents.installed.codex import Codex
    except ImportError:
        return

    if any(getattr(f, "kwarg", None) == "openai_base_url" for f in Codex.CLI_FLAGS):
        return

    Codex.CLI_FLAGS.append(
        CliFlag(
            kwarg="openai_base_url",
            cli="-c",
            type="str",
            env_fallback="OPENAI_BASE_URL",
            format='-c openai_base_url="{value}"',
        )
    )
    logger.debug("Registered OPENAI_BASE_URL forwarding flag on Harbor Codex agent")


def apply_all() -> None:
    """Apply every Harbor workaround. Safe to call multiple times."""
    _ensure_codex_openai_base_url_flag()
