"""Pure-logic tests for the runner module — no Harbor imports needed."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicraft.runner import (
    MissingRequiredModelError,
    _extract_final_text,
    _resolve_trajectory_dir,
    _temp_task_dir,
)


class TestResolveTrajectoryDir:
    def test_default_is_cwd_relative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AICRAFT_TRAJECTORY_DIR", raising=False)
        result = _resolve_trajectory_dir()
        assert result == Path.cwd() / "trajectories"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AICRAFT_TRAJECTORY_DIR", str(tmp_path))
        result = _resolve_trajectory_dir()
        assert result == tmp_path.resolve()

    def test_env_expands_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AICRAFT_TRAJECTORY_DIR", "~/test-trajectories")
        result = _resolve_trajectory_dir()
        assert "~" not in str(result)
        assert str(result).endswith("test-trajectories")


class TestTempTaskDir:
    """The synthesized task.toml has to thread caller knobs into Harbor."""

    def test_default_memory_is_4096(self) -> None:
        with _temp_task_dir(prompt="hello", memory_mb=4096) as task_dir:
            toml = (task_dir / "task.toml").read_text()
        assert "memory_mb = 4096" in toml

    def test_memory_override_is_written(self) -> None:
        with _temp_task_dir(prompt="hello", memory_mb=8192) as task_dir:
            toml = (task_dir / "task.toml").read_text()
        assert "memory_mb = 8192" in toml
        assert "memory_mb = 4096" not in toml

    def test_cleanup_removes_task_dir(self) -> None:
        with _temp_task_dir(prompt="hello", memory_mb=4096) as task_dir:
            assert task_dir.exists()
        assert not task_dir.exists()


class TestExtractFinalText:
    """Verifies ATIF parsing across both schema versions we care about."""

    def test_v1_2_claude_code(self, atif_v1_2_dir: Path) -> None:
        text = _extract_final_text(None, atif_v1_2_dir)
        # Real captured trajectory: claude-code's reply about zithers.
        assert "zither" in text.lower()
        assert text.strip(), "expected non-empty reply for ATIF v1.2"

    def test_v1_5_codex_with_tool_calls(self, atif_v1_5_dir: Path) -> None:
        text = _extract_final_text(None, atif_v1_5_dir)
        # Real captured trajectory: codex fixed a bug, replied with a code block.
        assert "def add" in text
        assert text.strip(), "expected non-empty reply for ATIF v1.5"

    def test_returns_empty_when_trajectory_missing(self, tmp_path: Path) -> None:
        assert _extract_final_text(None, tmp_path) == ""

    def test_returns_empty_for_malformed_json(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "trajectory.json").write_text("not json {{{")
        assert _extract_final_text(None, tmp_path) == ""

    def test_returns_empty_when_no_agent_steps(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "trajectory.json").write_text(
            '{"schema_version": "ATIF-v1.2", "steps": ['
            '{"step_id": 1, "source": "user", "message": "hi"}'
            "]}"
        )
        assert _extract_final_text(None, tmp_path) == ""

    def test_picks_last_agent_step(self, tmp_path: Path) -> None:
        # When multiple agent steps exist, the last one wins.
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "trajectory.json").write_text(
            '{"schema_version": "ATIF-v1.5", "steps": ['
            '{"step_id": 1, "source": "user", "message": "go"},'
            '{"step_id": 2, "source": "agent", "message": "intermediate"},'
            '{"step_id": 3, "source": "agent", "message": "final"}'
            "]}"
        )
        assert _extract_final_text(None, tmp_path) == "final"

    def test_ignores_non_string_message(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # Some agents emit structured content; we don't try to render it.
        (agent_dir / "trajectory.json").write_text(
            '{"schema_version": "ATIF-v1.5", "steps": ['
            '{"step_id": 1, "source": "agent", "message": {"parts": [1, 2]}}'
            "]}"
        )
        assert _extract_final_text(None, tmp_path) == ""


class TestMissingRequiredModel:
    """The fail-fast model check happens in AgentRunner.run().

    We verify the exception class is exported and a string-typed instance
    renders sensibly. End-to-end flow through ``AgentRunner.run`` requires
    Harbor + a docker daemon and is covered by integration runs, not unit
    tests.
    """

    def test_exception_message_renders(self) -> None:
        e = MissingRequiredModelError("agent 'codex' requires an explicit model")
        assert "codex" in str(e)
        assert isinstance(e, ValueError)
