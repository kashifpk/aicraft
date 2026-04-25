"""Mount-allowlist enforcement.

The set of allowed host paths is configured via the ``AICRAFT_MOUNT_ROOTS``
env var (colon-separated, like ``PATH``). Every host path in a job's mounts
is resolved (symlinks collapsed) and must sit under one of those roots —
otherwise the job is rejected before Harbor ever sees it.

Empty / unset means **no mounts allowed at all**. There is no wildcard;
deployments that need broader access set explicit roots that cover what
they need.
"""

from __future__ import annotations

import os
from pathlib import Path

from aicraft.types import MountSpec


class MountNotAllowedError(ValueError):
    """Raised when a MountSpec's host path escapes the configured allowlist."""


def load_allowed_roots(env_value: str | None = None) -> list[Path]:
    """Return the resolved list of allowed mount roots from the env."""
    raw = env_value if env_value is not None else os.environ.get("AICRAFT_MOUNT_ROOTS", "")
    if not raw.strip():
        return []
    roots: list[Path] = []
    for part in raw.split(":"):
        part = part.strip()
        if not part:
            continue
        # strict=False: a configured root that doesn't exist yet is still
        # valid (we'll just never match anything under it). Avoids a chicken-
        # and-egg on fresh hosts where the dir is created later by ops.
        roots.append(Path(part).resolve(strict=False))
    return roots


def _is_under(path: Path, root: Path) -> bool:
    """True if ``path`` is ``root`` or a descendant."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_mounts(mounts: list[MountSpec], allowed_roots: list[Path]) -> None:
    """Raise MountNotAllowedError if any mount's host path escapes the allowlist.

    Host paths are fully resolved (``Path.resolve(strict=True)``) so symlink
    trickery can't smuggle a mount past the check. A mount with a host path
    that doesn't exist yet is rejected — the caller should create the
    directory before running.
    """
    for m in mounts:
        try:
            resolved = m.host.resolve(strict=True)
        except FileNotFoundError as e:
            raise MountNotAllowedError(
                f"Mount host path does not exist: {m.host}"
            ) from e

        if not any(_is_under(resolved, root) for root in allowed_roots):
            raise MountNotAllowedError(
                f"Mount host path {resolved} is not under any allowed root. "
                f"Allowed roots: {[str(r) for r in allowed_roots] or '(none — set AICRAFT_MOUNT_ROOTS)'}"
            )
