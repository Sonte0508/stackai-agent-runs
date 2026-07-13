from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RunNotFoundError
from app.db.repository import RunRepository
from app.schemas.analytics import (
    AgentCostBreakdown,
    AnalyticsSummary,
    RunCostBreakdown,
    StepTypeBreakdown,
)


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


class AnalyticsService:
    """
    Turns raw run/step rows into the numbers a customer would actually act
    on: where cost concentrates (by agent, by step type), how reliable each
    step type is, and the run-duration distribution (p50/p95, not just the
    average - averages hide the slow tail that's usually what someone is
    debugging when they open this).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.repo = RunRepository(session)

    async def summary(
        self, *, since: datetime | None, until: datetime | None, agent: str | None
    ) -> AnalyticsSummary:
        runs = await self.repo.list_all_runs_for_analytics(since=since, until=until, agent=agent)

        success = [r for r in runs if r.status == "succeeded"]
        failed = [r for r in runs if r.status == "failed"]
        terminal = success + failed + [r for r in runs if r.status == "cancelled"]
        in_progress = [r for r in runs if r.status in ("queued", "running", "cancelling")]

        total_cost = sum(r.total_cost_usd for r in runs)
        total_in = sum(r.total_tokens_in for r in runs)
        total_out = sum(r.total_tokens_out for r in runs)

        durations = sorted(
            (r.completed_at - r.started_at).total_seconds() * 1000
            for r in terminal
            if r.started_at and r.completed_at
        )
        avg_duration = sum(durations) / len(durations) if durations else None

        by_agent: dict[str, list] = {}
        for r in runs:
            by_agent.setdefault(r.agent, []).append(r)
        cost_by_agent = [
            AgentCostBreakdown(
                agent=agent_name,
                run_count=len(agent_runs),
                total_cost_usd=round(sum(x.total_cost_usd for x in agent_runs), 6),
                total_tokens_in=sum(x.total_tokens_in for x in agent_runs),
                total_tokens_out=sum(x.total_tokens_out for x in agent_runs),
                avg_cost_per_run_usd=round(
                    sum(x.total_cost_usd for x in agent_runs) / len(agent_runs), 6
                ),
            )
            for agent_name, agent_runs in sorted(
                by_agent.items(), key=lambda kv: sum(x.total_cost_usd for x in kv[1]), reverse=True
            )
        ]

        # Step-type breakdown, across all runs in the window.
        step_buckets: dict[str, list] = {}
        for r in runs:
            for s in await self.repo.list_steps(r.id):
                step_buckets.setdefault(s.type, []).append(s)

        breakdown_by_step_type = []
        for step_type, steps in step_buckets.items():
            terminal_steps = [s for s in steps if s.status in ("succeeded", "failed")]
            failed_steps = [s for s in terminal_steps if s.status == "failed"]
            durs = [s.duration_ms for s in steps if s.duration_ms is not None]
            breakdown_by_step_type.append(
                StepTypeBreakdown(
                    type=step_type,
                    step_count=len(steps),
                    total_cost_usd=round(sum(s.cost_usd for s in steps), 6),
                    avg_duration_ms=round(sum(durs) / len(durs), 2) if durs else 0.0,
                    failure_rate=round(len(failed_steps) / len(terminal_steps), 4)
                    if terminal_steps
                    else 0.0,
                )
            )
        breakdown_by_step_type.sort(key=lambda b: b.total_cost_usd, reverse=True)

        return AnalyticsSummary(
            window_start=since.isoformat() if since else None,
            window_end=until.isoformat() if until else None,
            run_count=len(runs),
            success_count=len(success),
            failure_count=len(failed),
            in_progress_count=len(in_progress),
            success_rate=round(len(success) / len(terminal), 4) if terminal else 0.0,
            total_cost_usd=round(total_cost, 6),
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            avg_run_duration_ms=round(avg_duration, 2) if avg_duration else None,
            p50_run_duration_ms=_percentile(durations, 0.50),
            p95_run_duration_ms=_percentile(durations, 0.95),
            cost_by_agent=cost_by_agent,
            breakdown_by_step_type=breakdown_by_step_type,
        )

    async def run_cost_breakdown(self, run_id: str) -> RunCostBreakdown:
        record = await self.repo.get_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        steps = await self.repo.list_steps(run_id)
        return RunCostBreakdown(
            run_id=run_id,
            total_cost_usd=record.total_cost_usd,
            total_tokens_in=record.total_tokens_in,
            total_tokens_out=record.total_tokens_out,
            by_step=[
                {
                    "step_id": s.id,
                    "name": s.name,
                    "type": s.type,
                    "model": s.model,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "cost_usd": s.cost_usd,
                }
                for s in steps
            ],
        )
