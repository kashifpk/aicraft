"""Tests for the Harbor 0.4 monkey-patches.

These exercise patch idempotency and the env-gating without requiring a
real Harbor invocation. They DO need ``harbor`` installed in the test env
because they reach into ``harbor.agents.installed.codex.Codex.CLI_FLAGS``
to verify the patch took effect.
"""

from __future__ import annotations

import pytest

from aicraft._harbor_patches import _ensure_codex_openai_base_url_flag


def _flag_present() -> bool:
    from harbor.agents.installed.codex import Codex
    return any(getattr(f, "kwarg", None) == "openai_base_url" for f in Codex.CLI_FLAGS)


def _remove_flag() -> None:
    """Strip the patched flag so each test starts from a clean baseline."""
    from harbor.agents.installed.codex import Codex
    Codex.CLI_FLAGS = [
        f for f in Codex.CLI_FLAGS if getattr(f, "kwarg", None) != "openai_base_url"
    ]


@pytest.fixture(autouse=True)
def _clean_codex_flags():
    """Each test runs with no openai_base_url flag pre-registered."""
    _remove_flag()
    yield
    _remove_flag()


def test_no_op_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    _ensure_codex_openai_base_url_flag()
    assert not _flag_present()


def test_registers_flag_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")
    _ensure_codex_openai_base_url_flag()
    assert _flag_present()


def test_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")
    _ensure_codex_openai_base_url_flag()
    _ensure_codex_openai_base_url_flag()
    _ensure_codex_openai_base_url_flag()

    from harbor.agents.installed.codex import Codex
    matching = [f for f in Codex.CLI_FLAGS if getattr(f, "kwarg", None) == "openai_base_url"]
    assert len(matching) == 1
