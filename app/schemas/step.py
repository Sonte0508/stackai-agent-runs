from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class StepType(str, Enum):
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    SUB_AGENT = "sub_agent"


class StepStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"


class Step(BaseModel):
    id: str
    run_id: str
    seq: int
    type: StepType
    name: str
    status: StepStatus
    model: str | None = None
    attempt: int
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None
    span_id: str | None = None

    model_config = {"from_attributes": True}


class StepList(BaseModel):
    data: list[Step]


# --- Server-Sent Events payloads (documented for SDK generators, not part of OpenAPI schema) ---


class RunEvent(BaseModel):
    event: str  # "step.started" | "step.succeeded" | "step.failed" | "step.retrying" | "run.completed"
    run_id: str
    step: Step | None = None
    run: dict[str, Any] | None = None
    timestamp: datetime
