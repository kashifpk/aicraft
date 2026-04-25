"""Test fixtures shared across the suite."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def atif_v1_2_dir(tmp_path: Path) -> Path:
    """A trajectory_dir layout containing a real ATIF v1.2 trajectory.

    Mirrors the on-disk shape ``AgentRunner`` produces: ``<dir>/agent/trajectory.json``.
    Captured from a real Claude Code run on harbor 0.4.
    """
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "trajectory.json").write_bytes(
        (FIXTURES / "atif_v1_2_claude_code.json").read_bytes()
    )
    return tmp_path


@pytest.fixture
def atif_v1_5_dir(tmp_path: Path) -> Path:
    """A trajectory_dir layout containing a real ATIF v1.5 trajectory.

    Captured from a real Codex run on harbor 0.4 with tool calls present.
    """
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "trajectory.json").write_bytes(
        (FIXTURES / "atif_v1_5_codex.json").read_bytes()
    )
    return tmp_path
