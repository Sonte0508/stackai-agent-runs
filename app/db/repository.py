from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RunRecord, StepRecord


class RunRepository:
    """All persistence access for runs/steps. Keeps the ORM out of services."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Runs ---

    async def create_run(self, run: RunRecord) -> RunRecord:
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_run(self, run_id: str) -> RunRecord | None:
        result = await self.session.execute(select(RunRecord).where(RunRecord.id == run_id))
        return result.scalar_one_or_none()

    async def get_run_by_idempotency_key(self, key: str, agent: str) -> RunRecord | None:
        result = await self.session.execute(
            select(RunRecord).where(
                RunRecord.idempotency_key == key, RunRecord.agent == agent
            )
        )
        return result.scalar_one_or_none()

    async def list_runs(
        self,
        *,
        status: str | None,
        agent: str | None,
        limit: int,
        before_created_at: datetime | None,
    ) -> list[RunRecord]:
        stmt = select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(RunRecord.status == status)
        if agent:
            stmt = stmt.where(RunRecord.agent == agent)
        if before_created_at:
            stmt = stmt.where(RunRecord.created_at < before_created_at)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_runs_for_analytics(
        self, *, since: datetime | None, until: datetime | None, agent: str | None
    ) -> list[RunRecord]:
        stmt = select(RunRecord)
        if since:
            stmt = stmt.where(RunRecord.created_at >= since)
        if until:
            stmt = stmt.where(RunRecord.created_at <= until)
        if agent:
            stmt = stmt.where(RunRecord.agent == agent)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Steps ---

    async def add_step(self, step: StepRecord) -> StepRecord:
        self.session.add(step)
        await self.session.flush()
        return step

    async def list_steps(self, run_id: str) -> list[StepRecord]:
        result = await self.session.execute(
            select(StepRecord).where(StepRecord.run_id == run_id).order_by(StepRecord.seq)
        )
        return list(result.scalars().all())

    async def commit(self) -> None:
        await self.session.commit()
