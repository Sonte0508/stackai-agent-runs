from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_idempotency_key, get_session
from app.config import get_settings
from app.core.errors import RunNotFoundError
from app.db.repository import RunRepository
from app.schemas.run import CreateRunRequest, Run, RunList
from app.schemas.step import StepList
from app.services.pubsub import event_bus
from app.services.run_service import RunService

router = APIRouter(prefix="/runs", tags=["runs"])
settings = get_settings()


@router.post(
    "",
    response_model=Run,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a run",
    description=(
        "Starts a new agent run and returns immediately with status 'queued'. "
        "Runs execute asynchronously - poll GET /runs/{id}, stream "
        "GET /runs/{id}/events, or check back later. Pass an `Idempotency-Key` "
        "header to safely retry a create call (e.g. after a network timeout) "
        "without starting a duplicate run."
    ),
)
async def create_run(
    payload: CreateRunRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Depends(get_idempotency_key),
) -> Run:
    service = RunService(session)
    run, was_replayed = await service.create_run(payload, idempotency_key)
    if was_replayed:
        response.headers["Idempotency-Replayed"] = "true"
    response.headers["Location"] = f"/v1/runs/{run.id}"
    return run


@router.get(
    "/{run_id}",
    response_model=Run,
    summary="Get a run",
    description="Fetch the current state of a run, including cost totals and its trace id.",
)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> Run:
    service = RunService(session)
    return await service.get_run(run_id)


@router.get(
    "",
    response_model=RunList,
    summary="List runs",
    description="List runs, most recent first. Use `cursor` from a previous "
    "response's `next_cursor` to page.",
)
async def list_runs(
    session: AsyncSession = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status"),
    agent: str | None = Query(default=None),
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    cursor: str | None = Query(default=None),
) -> RunList:
    service = RunService(session)
    return await service.list_runs(status=status_filter, agent=agent, limit=limit, cursor=cursor)


@router.get(
    "/{run_id}/steps",
    response_model=StepList,
    summary="List a run's steps",
    description="All steps executed so far for this run, in order.",
)
async def list_run_steps(run_id: str, session: AsyncSession = Depends(get_session)) -> StepList:
    service = RunService(session)
    steps = await service.list_steps(run_id)
    return StepList(data=steps)


@router.post(
    "/{run_id}/cancel",
    response_model=Run,
    summary="Cancel a run",
    description="Requests cancellation of an in-progress run. The current step "
    "finishes, then no further steps start. Returns 409 if the run has "
    "already reached a terminal state.",
)
async def cancel_run(run_id: str, session: AsyncSession = Depends(get_session)) -> Run:
    service = RunService(session)
    return await service.cancel_run(run_id)


@router.get(
    "/{run_id}/events",
    summary="Follow a run's progress (Server-Sent Events)",
    description=(
        "Streams step lifecycle events as they happen: `step.started`, "
        "`step.succeeded`, `step.failed`, `step.retrying`, and a final "
        "`run.completed`. On connect, already-completed steps are replayed "
        "first so a late subscriber still sees the full picture, then the "
        "stream tails live and closes once the run reaches a terminal state."
    ),
    response_class=StreamingResponse,
)
async def stream_run_events(run_id: str, session: AsyncSession = Depends(get_session)):
    repo = RunRepository(session)
    record = await repo.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)

    backlog_steps = await repo.list_steps(run_id)
    is_terminal = record.status in ("succeeded", "failed", "cancelled")

    async def event_generator():
        for s in backlog_steps:
            payload = {
                "event": f"step.{s.status}",
                "run_id": run_id,
                "step": {
                    "id": s.id,
                    "type": s.type,
                    "name": s.name,
                    "status": s.status,
                    "attempt": s.attempt,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "cost_usd": s.cost_usd,
                    "duration_ms": s.duration_ms,
                },
                "timestamp": (s.ended_at or s.started_at).isoformat(),
                "replay": True,
            }
            yield f"data: {json.dumps(payload)}\n\n"

        if is_terminal:
            yield f"data: {json.dumps({'event': 'run.completed', 'run_id': run_id, 'run': {'status': record.status}})}\n\n"
            return

        queue = event_bus.subscribe(run_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event.get("event") == "run.completed":
                    break
        finally:
            event_bus.unsubscribe(run_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
