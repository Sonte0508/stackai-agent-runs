from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_session
from app.schemas.analytics import AnalyticsSummary, RunCostBreakdown
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get(
    "/summary",
    response_model=AnalyticsSummary,
    summary="Aggregate cost, reliability, and latency analytics",
    description=(
        "The view a customer would actually check: total spend and success "
        "rate for the window, cost broken down by agent (where is the money "
        "going), reliability and latency broken down by step type (what's "
        "actually flaky or slow), and the run-duration distribution "
        "(p50/p95, not just an average that hides the slow tail)."
    ),
)
async def analytics_summary(
    session: AsyncSession = Depends(get_session),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    agent: str | None = Query(default=None),
) -> AnalyticsSummary:
    service = AnalyticsService(session)
    return await service.summary(since=since, until=until, agent=agent)


@router.get(
    "/runs/{run_id}/cost",
    response_model=RunCostBreakdown,
    summary="Per-step cost breakdown for one run",
    description="Exactly where a single run's cost came from, step by step.",
)
async def run_cost_breakdown(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunCostBreakdown:
    service = AnalyticsService(session)
    return await service.run_cost_breakdown(run_id)
