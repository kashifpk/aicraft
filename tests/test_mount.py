"""Mount allowlist behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicraft.mount import (
    MountNotAllowedError,
    _is_under,
    load_allowed_roots,
    validate_mounts,
)
from aicraft.types import MountSpec


class TestLoadAllowedRoots:
    def test_empty_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AICRAFT_MOUNT_ROOTS", raising=False)
        assert load_allowed_roots() == []

    def test_empty_string_means_empty_list(self) -> None:
        assert load_allowed_roots("") == []
        assert load_allowed_roots("   ") == []

    def test_single_root(self, tmp_path: Path) -> None:
        roots = load_allowed_roots(str(tmp_path))
        assert roots == [tmp_path.resolve()]

    def test_colon_separated(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        roots = load_allowed_roots(f"{a}:{b}")
        assert roots == [a.resolve(), b.resolve()]

    def test_skips_empty_segments(self, tmp_path: Path) -> None:
        roots = load_allowed_roots(f"::{tmp_path}::")
        assert roots == [tmp_path.resolve()]

    def test_nonexistent_root_still_returned(self) -> None:
        # We don't require roots to exist at config time — ops may create them
        # lazily. Mount validation will still reject any path that doesn't
        # actually live under one.
        roots = load_allowed_roots("/does/not/exist/yet")
        assert roots == [Path("/does/not/exist/yet").resolve(strict=False)]


class TestIsUnder:
    def test_same_path(self, tmp_path: Path) -> None:
        assert _is_under(tmp_path, tmp_path)

    def test_descendant(self, tmp_path: Path) -> None:
        child = tmp_path / "a" / "b"
        assert _is_under(child, tmp_path)

    def test_sibling(self, tmp_path: Path) -> None:
        sibling = tmp_path.parent / "other"
        assert not _is_under(sibling, tmp_path)


class TestValidateMounts:
    def test_empty_mounts_no_op_even_with_no_roots(self) -> None:
        # No mounts → nothing to check → no allowlist needed.
        validate_mounts([], [])

    def test_accepts_path_under_allowed_root(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        m = MountSpec(host=sub, container=Path("/workspace"))
        validate_mounts([m], [tmp_path])

    def test_rejects_path_outside_roots(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        m = MountSpec(host=outside, container=Path("/workspace"))
        with pytest.raises(MountNotAllowedError, match="not under any allowed root"):
            validate_mounts([m], [unrelated])

    def test_rejects_when_no_roots_configured(self, tmp_path: Path) -> None:
        m = MountSpec(host=tmp_path, container=Path("/workspace"))
        with pytest.raises(MountNotAllowedError, match="set AICRAFT_MOUNT_ROOTS"):
            validate_mounts([m], [])

    def test_rejects_nonexistent_host_path(self, tmp_path: Path) -> None:
        m = MountSpec(
            host=tmp_path / "does-not-exist",
            container=Path("/workspace"),
        )
        with pytest.raises(MountNotAllowedError, match="does not exist"):
            validate_mounts([m], [tmp_path])

    def test_resolves_symlinks_before_comparison(self, tmp_path: Path) -> None:
        # Symlink trickery shouldn't smuggle a mount past the check. Create
        # a directory outside the allowed root, point a symlink at it from
        # *inside* the allowed root, and verify the validator follows the
        # symlink to reject the real target.
        outside = tmp_path / "outside"
        outside.mkdir()
        inside = tmp_path / "inside"
        inside.mkdir()
        link = inside / "trick"
        link.symlink_to(outside)

        m = MountSpec(host=link, container=Path("/workspace"))
        with pytest.raises(MountNotAllowedError, match="not under any allowed root"):
            validate_mounts([m], [inside])
