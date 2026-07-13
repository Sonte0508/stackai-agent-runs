from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class CreateRunRequest(BaseModel):
    agent: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Identifier of the agent configuration to run.",
        examples=["research-assistant"],
    )
    input: dict[str, Any] = Field(
        ...,
        description="Arbitrary JSON payload passed to the agent as its task input.",
        examples=[{"query": "Summarize our Q2 churn drivers"}],
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Caller-supplied tags echoed back on the run, e.g. {'customer_id': '...'}.",
    )
    seed: int | None = Field(
        default=None,
        description=(
            "Optional seed for the fake runner. Omit to get a seed derived from the "
            "run id (still reproducible if you replay with the returned seed)."
        ),
    )

    @field_validator("agent")
    @classmethod
    def agent_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("agent must not be blank")
        return v


class RunError(BaseModel):
    code: str
    message: str


class RunCost(BaseModel):
    tokens_in: int
    tokens_out: int
    total_cost_usd: float = Field(..., description="Simulated cost in USD, 6 decimal places.")


class Run(BaseModel):
    id: str
    agent: str
    api_version: str
    status: RunStatus
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    error: RunError | None = None
    metadata: dict[str, Any]
    seed: int
    cost: RunCost
    step_count: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    trace_id: str | None = Field(
        default=None,
        description="OpenTelemetry trace id for this run, to jump straight into your backend.",
    )

    model_config = {"from_attributes": True}


class RunList(BaseModel):
    data: list[Run]
    next_cursor: str | None = Field(
        default=None, description="Pass as ?cursor= to fetch the next page, if present."
    )
    has_more: bool
