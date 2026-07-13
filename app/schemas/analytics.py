from __future__ import annotations

from pydantic import BaseModel


class AgentCostBreakdown(BaseModel):
    agent: str
    run_count: int
    total_cost_usd: float
    total_tokens_in: int
    total_tokens_out: int
    avg_cost_per_run_usd: float


class StepTypeBreakdown(BaseModel):
    type: str
    step_count: int
    total_cost_usd: float
    avg_duration_ms: float
    failure_rate: float


class AnalyticsSummary(BaseModel):
    window_start: str | None
    window_end: str | None
    run_count: int
    success_count: int
    failure_count: int
    in_progress_count: int
    success_rate: float
    total_cost_usd: float
    total_tokens_in: int
    total_tokens_out: int
    avg_run_duration_ms: float | None
    p50_run_duration_ms: float | None
    p95_run_duration_ms: float | None
    cost_by_agent: list[AgentCostBreakdown]
    breakdown_by_step_type: list[StepTypeBreakdown]


class RunCostBreakdown(BaseModel):
    run_id: str
    total_cost_usd: float
    total_tokens_in: int
    total_tokens_out: int
    by_step: list[dict]
