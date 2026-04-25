"""CLI-side tests — focus on the parts that don't need a real Harbor run."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicraft.cli import (
    _PROVIDER_PRESETS,
    _apply_provider_preset,
    _parse_mount,
    _PresetConfigError,
)
from aicraft.types import MountSpec


class TestParseMount:
    def test_default_mode_is_ro(self) -> None:
        m = _parse_mount("/host:/container")
        assert m == MountSpec(host=Path("/host"), container=Path("/container"), read_only=True)

    def test_explicit_ro(self) -> None:
        m = _parse_mount("/host:/container:ro")
        assert m.read_only is True

    def test_explicit_rw(self) -> None:
        m = _parse_mount("/host:/container:rw")
        assert m.read_only is False

    def test_too_few_parts(self) -> None:
        with pytest.raises(ValueError, match="Expected host:container"):
            _parse_mount("/just-host")

    def test_too_many_parts(self) -> None:
        with pytest.raises(ValueError, match="Expected host:container"):
            _parse_mount("/host:/container:ro:extra")

    def test_invalid_mode(self) -> None:
        with pytest.raises(ValueError, match="Mount mode must be"):
            _parse_mount("/host:/container:wat")


class TestApplyProviderPreset:
    """Each preset rewrites OPENAI_BASE_URL + OPENAI_API_KEY from a source key."""

    @pytest.mark.parametrize("preset_name", sorted(_PROVIDER_PRESETS))
    def test_known_preset_with_key_set(
        self,
        preset_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        preset = _PROVIDER_PRESETS[preset_name]
        monkeypatch.setenv(preset["key_source"], "test-key-value")
        # Clear OPENAI_* to verify the preset writes them
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        _apply_provider_preset(preset_name)

        import os

        assert os.environ["OPENAI_BASE_URL"] == preset["base_url"]
        assert os.environ["OPENAI_API_KEY"] == "test-key-value"

    def test_unknown_preset_rejected(self) -> None:
        with pytest.raises(_PresetConfigError, match="unknown provider"):
            _apply_provider_preset("not-a-real-thing")

    def test_missing_source_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(_PresetConfigError, match="OPENROUTER_API_KEY"):
            _apply_provider_preset("openrouter")
