from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import IdempotencyKeyReuseError, InvalidRunStateError, RunNotFoundError
from app.db.models import RunRecord, RunStatus
from app.db.repository import RunRepository
from app.schemas.run import CreateRunRequest, Run, RunCost, RunError, RunList
from app.schemas.step import Step
from app.services import runner as runner_module

_background_tasks: set[asyncio.Task] = set()


def _run_id() -> str:
    return f"run_{uuid.uuid4().hex[:24]}"


def _to_run_schema(record: RunRecord, step_count: int) -> Run:
    duration_ms = None
    if record.started_at and record.completed_at:
        duration_ms = (record.completed_at - record.started_at).total_seconds() * 1000
    return Run(
        id=record.id,
        agent=record.agent,
        api_version=record.api_version,
        status=RunStatus(record.status),
        input=record.input,
        output=record.output,
        error=(
            RunError(code=record.error_code, message=record.error_message)
            if record.error_code
            else None
        ),
        metadata=record.run_metadata,
        seed=record.seed,
        cost=RunCost(
            tokens_in=record.total_tokens_in,
            tokens_out=record.total_tokens_out,
            total_cost_usd=record.total_cost_usd,
        ),
        step_count=step_count,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
        trace_id=record.trace_id,
    )


class RunService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = RunRepository(session)

    async def create_run(
        self, payload: CreateRunRequest, idempotency_key: str | None
    ) -> tuple[Run, bool]:
        """Returns (run, was_replayed). was_replayed=True means an existing
        run with the same Idempotency-Key was returned instead of a new one."""
        if idempotency_key:
            existing = await self.repo.get_run_by_idempotency_key(idempotency_key, payload.agent)
            if existing is not None:
                return await self._replay_or_conflict(existing, payload)

        run_id = _run_id()
        seed = payload.seed if payload.seed is not None else int.from_bytes(
            run_id.encode()[-4:], "big"
        )
        record = RunRecord(
            id=run_id,
            agent=payload.agent,
            api_version="v1",
            status=RunStatus.QUEUED.value,
            input=payload.input,
            run_metadata=payload.metadata,
            seed=seed,
            idempotency_key=idempotency_key,
            created_at=datetime.utcnow(),
        )
        try:
            await self.repo.create_run(record)
            await self.repo.commit()
        except IntegrityError:
            # Two concurrent requests with the same Idempotency-Key both
            # passed the check above before either committed - the DB's
            # unique constraint on (agent, idempotency_key) is what actually
            # prevents the duplicate run. Whoever loses the race falls back
            # to replaying the winner's row, same as the pre-check path.
            await self.repo.session.rollback()
            existing = await self.repo.get_run_by_idempotency_key(idempotency_key, payload.agent)
            if existing is None:
                raise
            return await self._replay_or_conflict(existing, payload)

        task = asyncio.create_task(runner_module.execute_run(run_id, payload.agent, seed))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return _to_run_schema(record, 0), False

    async def _replay_or_conflict(
        self, existing: RunRecord, payload: CreateRunRequest
    ) -> tuple[Run, bool]:
        if existing.input != payload.input:
            raise IdempotencyKeyReuseError(
                "This Idempotency-Key was already used with a different request body."
            )
        return _to_run_schema(existing, len(await self.repo.list_steps(existing.id))), True

    async def get_run(self, run_id: str) -> Run:
        record = await self.repo.get_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        steps = await self.repo.list_steps(run_id)
        return _to_run_schema(record, len(steps))

    async def list_steps(self, run_id: str) -> list[Step]:
        record = await self.repo.get_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        steps = await self.repo.list_steps(run_id)
        return [Step.model_validate(s) for s in steps]

    async def list_runs(
        self, *, status: str | None, agent: str | None, limit: int, cursor: str | None
    ) -> RunList:
        before_created_at = None
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                before_created_at = datetime.fromisoformat(decoded["before"])
            except Exception:
                before_created_at = None

        records = await self.repo.list_runs(
            status=status, agent=agent, limit=limit + 1, before_created_at=before_created_at
        )
        has_more = len(records) > limit
        records = records[:limit]

        runs = []
        for r in records:
            steps = await self.repo.list_steps(r.id)
            runs.append(_to_run_schema(r, len(steps)))

        next_cursor = None
        if has_more and records:
            payload = json.dumps({"before": records[-1].created_at.isoformat()})
            next_cursor = base64.urlsafe_b64encode(payload.encode()).decode()

        return RunList(data=runs, next_cursor=next_cursor, has_more=has_more)

    async def cancel_run(self, run_id: str) -> Run:
        record = await self.repo.get_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        if record.status in (RunStatus.SUCCEEDED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value):
            raise InvalidRunStateError(
                f"Run is already in terminal state '{record.status}' and cannot be cancelled."
            )
        accepted = runner_module.request_cancel(run_id)
        if accepted and record.status == RunStatus.QUEUED.value:
            # Not started yet (rare race) - nothing to interrupt mid-step.
            pass
        record.status = RunStatus.CANCELLING.value
        await self.repo.commit()
        steps = await self.repo.list_steps(run_id)
        return _to_run_schema(record, len(steps))
