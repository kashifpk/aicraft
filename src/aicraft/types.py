"""Input/output schemas for AgentRunner.

Pydantic models so they can be serialized over RMQ / HTTP without reshaping
when callers need to drive the runner from a different process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MountSpec(BaseModel):
    """A single bind mount from host to container.

    Fields mirror Docker compose's service-volume shape for straightforward
    translation into Harbor's EnvironmentConfig.mounts_json.
    """

    host: Path = Field(..., description="Absolute host path. Must live under an allowed root.")
    container: Path = Field(..., description="Absolute mount point inside the sandbox.")
    read_only: bool = True

    model_config = ConfigDict(frozen=True)


class AgentConfig(BaseModel):
    """Everything needed to run one agent invocation."""

    prompt: str
    agent: str = "claude-code"  # matches Harbor's installed agent names
    model: str | None = None
    mounts: list[MountSpec] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict, description="Passed through to the agent")
    timeout_s: int = 600
    memory_mb: int = Field(
        default=4096,
        ge=64,
        description="Container memory limit in MB. Bump for memory-hungry agents/models.",
    )

    model_config = ConfigDict(extra="forbid")


class AgentResult(BaseModel):
    """Outcome of one agent run."""

    status: Literal["completed", "timeout", "error"]
    final_text: str = Field(default="", description="Agent's final textual output, if captured")
    trajectory_path: Path | None = Field(
        default=None,
        description="On-disk path to the captured ATIF trajectory + agent session logs",
    )
    duration_s: float
    trial_id: str
    error: str | None = None

    model_config = ConfigDict(extra="forbid")
