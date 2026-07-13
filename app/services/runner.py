"""
The fake agent runner.

Given a run id, agent name, input, and seed, this executes a small,
reproducible plan of steps (model calls, tool calls, occasionally a
sub-agent), each with simulated latency/tokens/cost and an occasional
failure-then-retry. It's deliberately not a real agent loop - the point
is to produce realistic *shape* of data for the trace and analytics layers
to work with.

Every step is:
  1. Wrapped in its own OTel span, child of the run's root span.
  2. Persisted as a Step row as it starts and again as it finishes.
  3. Published on the run's event bus so SSE subscribers see it live.

The whole run is wrapped in a root span with no parent (it must survive
past the HTTP request that started it), and emits duration/cost/token
metrics on completion.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import datetime

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.config import get_settings
from app.db.models import RunRecord, RunStatus, StepRecord, StepStatus, StepType
from app.db.session import SessionScope
from app.db.repository import RunRepository
from app.services import cost as cost_model
from app.services.pubsub import event_bus
from app import telemetry
from app.telemetry import get_tracer

settings = get_settings()

# run_id -> cancellation flag, checked between steps.
_cancel_flags: dict[str, asyncio.Event] = {}


def request_cancel(run_id: str) -> bool:
    flag = _cancel_flags.get(run_id)
    if flag is None:
        return False
    flag.set()
    return True


def _sim_sleep_seconds(base_ms: float, rng: random.Random) -> float:
    jittered_ms = base_ms * rng.uniform(0.8, 1.3)
    return (jittered_ms / 1000.0) / settings.runner_speed_factor


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _plan_steps(rng: random.Random) -> list[dict]:
    """Build a reproducible step plan from the seeded RNG."""
    plan: list[dict] = [
        {"type": StepType.MODEL_CALL, "name": "plan_task", "model": "sim-fast", "base_ms": 600},
    ]
    n_tools = rng.randint(1, 3)
    for i in range(n_tools):
        plan.append(
            {"type": StepType.TOOL_CALL, "name": f"tool_call_{i + 1}", "base_ms": 350}
        )
    if rng.random() < 0.4:
        plan.append(
            {"type": StepType.SUB_AGENT, "name": "delegate_subtask", "base_ms": 1800}
        )
    plan.append(
        {
            "type": StepType.MODEL_CALL,
            "name": "synthesize_answer",
            "model": "sim-standard" if rng.random() < 0.7 else "sim-reasoning",
            "base_ms": 1200,
        }
    )
    return plan


async def _persist_and_publish(run_id: str, step: StepRecord, event: str) -> None:
    async with SessionScope() as session:
        repo = RunRepository(session)
        existing = None
        for s in await repo.list_steps(run_id):
            if s.id == step.id:
                existing = s
                break
        if existing is None:
            await repo.add_step(step)
        else:
            for field in (
                "status", "output", "error_code", "error_message",
                "tokens_in", "tokens_out", "cost_usd", "ended_at",
                "duration_ms", "attempt", "span_id",
            ):
                setattr(existing, field, getattr(step, field))
    await event_bus.publish(
        run_id,
        {
            "event": event,
            "run_id": run_id,
            "step": {
                "id": step.id,
                "type": step.type,
                "name": step.name,
                "status": step.status,
                "attempt": step.attempt,
                "tokens_in": step.tokens_in,
                "tokens_out": step.tokens_out,
                "cost_usd": step.cost_usd,
                "duration_ms": step.duration_ms,
                "error_message": step.error_message,
            },
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


async def _run_step(
    run_id: str,
    seq: int,
    spec: dict,
    rng: random.Random,
    parent_ctx,
    tracer,
) -> StepRecord:
    step_id = _new_id("step")
    step_type: StepType = spec["type"]
    max_retries = settings.runner_max_retries

    for attempt in range(1, max_retries + 2):  # allow max_retries retries after first attempt
        step = StepRecord(
            id=step_id,
            run_id=run_id,
            seq=seq,
            type=step_type.value,
            name=spec["name"],
            status=StepStatus.RUNNING.value,
            model=spec.get("model"),
            attempt=attempt,
            input={"note": f"simulated input for {spec['name']}"},
            started_at=datetime.utcnow(),
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
        )
        await _persist_and_publish(run_id, step, "step.started")

        with tracer.start_as_current_span(
            f"agent.step.{step_type.value}", context=parent_ctx
        ) as span:
            span.set_attribute("stackai.step.id", step_id)
            span.set_attribute("stackai.step.name", spec["name"])
            span.set_attribute("stackai.step.type", step_type.value)
            span.set_attribute("stackai.step.attempt", attempt)

            start = time.monotonic()
            await asyncio.sleep(_sim_sleep_seconds(spec["base_ms"], rng))

            fails = rng.random() < settings.runner_failure_rate
            # Only tool calls and sub-agents fail in this simulation - model
            # calls are treated as reliable, which mirrors reality reasonably
            # well (the interesting failures are usually downstream tools).
            will_fail = fails and step_type in (StepType.TOOL_CALL, StepType.SUB_AGENT)

            duration_ms = (time.monotonic() - start) * 1000
            step.duration_ms = round(duration_ms, 2)
            step.ended_at = datetime.utcnow()
            step.span_id = format(span.get_span_context().span_id, "016x")

            if will_fail:
                step.status = StepStatus.FAILED.value
                step.error_code = "tool_transient_error"
                step.error_message = f"{spec['name']} failed (simulated transient error)"
                span.set_status(Status(StatusCode.ERROR, step.error_message))
                span.set_attribute("stackai.step.error_code", step.error_code)
                await _persist_and_publish(run_id, step, "step.failed")

                if attempt <= max_retries:
                    step.status = StepStatus.RETRYING.value
                    telemetry.step_retry_counter.add(1, {"agent_step_type": step_type.value})
                    await _persist_and_publish(run_id, step, "step.retrying")
                    backoff = (0.2 * (2 ** (attempt - 1))) / settings.runner_speed_factor
                    await asyncio.sleep(backoff)
                    continue
                else:
                    return step  # exhausted retries, step ends in FAILED

            # success path
            if step_type == StepType.MODEL_CALL:
                tokens_in = rng.randint(200, 2000)
                tokens_out = rng.randint(50, 800)
                step.tokens_in = tokens_in
                step.tokens_out = tokens_out
                step.cost_usd = cost_model.model_call_cost(spec["model"], tokens_in, tokens_out)
                step.output = {"summary": f"simulated output for {spec['name']}"}
                span.set_attribute("gen_ai.system", "stackai-sim")
                span.set_attribute("gen_ai.request.model", spec["model"])
                span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
                span.set_attribute("gen_ai.usage.output_tokens", tokens_out)
            elif step_type == StepType.TOOL_CALL:
                step.cost_usd = cost_model.tool_call_cost()
                step.output = {"result": f"simulated tool result for {spec['name']}"}
                span.set_attribute("stackai.tool.name", spec["name"])
            else:  # SUB_AGENT: nest two synthetic child spans for trace depth
                with tracer.start_as_current_span("agent.sub_step.model_call") as sub1:
                    sub_tokens_in, sub_tokens_out = rng.randint(150, 600), rng.randint(50, 300)
                    sub1.set_attribute("gen_ai.request.model", "sim-fast")
                    sub1.set_attribute("gen_ai.usage.input_tokens", sub_tokens_in)
                    sub1.set_attribute("gen_ai.usage.output_tokens", sub_tokens_out)
                    await asyncio.sleep(_sim_sleep_seconds(300, rng))
                with tracer.start_as_current_span("agent.sub_step.tool_call") as sub2:
                    sub2.set_attribute("stackai.tool.name", "sub_agent_tool")
                    await asyncio.sleep(_sim_sleep_seconds(200, rng))
                sub_cost = cost_model.model_call_cost("sim-fast", sub_tokens_in, sub_tokens_out)
                sub_cost += cost_model.tool_call_cost()
                step.tokens_in = sub_tokens_in
                step.tokens_out = sub_tokens_out
                step.cost_usd = round(sub_cost, 6)
                step.output = {"delegated_result": f"simulated sub-agent result for {spec['name']}"}
                span.set_attribute("stackai.sub_agent.step_count", 2)

            step.status = StepStatus.SUCCEEDED.value
            span.set_status(Status(StatusCode.OK))
            telemetry.token_counter.add(step.tokens_in, {"direction": "input"})
            telemetry.token_counter.add(step.tokens_out, {"direction": "output"})
            telemetry.cost_counter.add(step.cost_usd, {"agent_step_type": step_type.value})
            await _persist_and_publish(run_id, step, "step.succeeded")
            return step

    return step  # pragma: no cover - loop always returns above


async def execute_run(run_id: str, agent: str, seed: int) -> None:
    """Entry point scheduled as a background asyncio task per run."""
    cancel_flag = asyncio.Event()
    _cancel_flags[run_id] = cancel_flag
    rng = random.Random(seed)
    tracer = get_tracer()

    root_ctx = otel_context.Context()  # empty context -> new trace, no parent
    with tracer.start_as_current_span(
        "agent.run", context=root_ctx, kind=trace.SpanKind.SERVER
    ) as root_span:
        trace_id = format(root_span.get_span_context().trace_id, "032x")
        run_ctx = trace.set_span_in_context(root_span)
        root_span.set_attribute("stackai.run.id", run_id)
        root_span.set_attribute("stackai.agent", agent)
        root_span.set_attribute("stackai.run.seed", seed)

        async with SessionScope() as session:
            repo = RunRepository(session)
            run = await repo.get_run(run_id)
            run.status = RunStatus.RUNNING.value
            run.started_at = datetime.utcnow()
            run.trace_id = trace_id

        start = time.monotonic()
        plan = _plan_steps(rng)
        final_status = RunStatus.SUCCEEDED
        total_tokens_in = total_tokens_out = 0
        total_cost = 0.0

        for seq, spec in enumerate(plan):
            if cancel_flag.is_set():
                final_status = RunStatus.CANCELLED
                break

            step = await _run_step(run_id, seq, spec, rng, run_ctx, tracer)
            total_tokens_in += step.tokens_in
            total_tokens_out += step.tokens_out
            total_cost += step.cost_usd

            if step.status == StepStatus.FAILED.value:
                final_status = RunStatus.FAILED
                break

        if final_status == RunStatus.SUCCEEDED and cancel_flag.is_set():
            # Cancellation can land after the last step already finished but
            # before we get here - catch that window instead of reporting
            # "succeeded" for a run the caller asked to stop.
            final_status = RunStatus.CANCELLED

        duration_ms = (time.monotonic() - start) * 1000
        root_span.set_attribute("stackai.run.status", final_status.value)
        root_span.set_attribute("stackai.run.total_cost_usd", round(total_cost, 6))
        if final_status == RunStatus.FAILED:
            root_span.set_status(Status(StatusCode.ERROR, "run failed: a step exhausted retries"))
        else:
            root_span.set_status(Status(StatusCode.OK))

        async with SessionScope() as session:
            repo = RunRepository(session)
            run = await repo.get_run(run_id)
            run.status = final_status.value
            run.completed_at = datetime.utcnow()
            run.total_tokens_in = total_tokens_in
            run.total_tokens_out = total_tokens_out
            run.total_cost_usd = round(total_cost, 6)
            if final_status == RunStatus.SUCCEEDED:
                run.output = {"result": f"simulated final output for agent '{agent}'"}
            elif final_status == RunStatus.FAILED:
                run.error_code = "run_failed"
                run.error_message = "A step exhausted its retries. See steps for detail."
            elif final_status == RunStatus.CANCELLED:
                run.error_code = "run_cancelled"
                run.error_message = "Run was cancelled by the caller."

        telemetry.run_duration_histogram.record(duration_ms, {"agent": agent, "status": final_status.value})
        telemetry.run_counter.add(1, {"agent": agent, "status": final_status.value})

        await event_bus.publish(
            run_id,
            {
                "event": "run.completed",
                "run_id": run_id,
                "run": {"status": final_status.value, "total_cost_usd": round(total_cost, 6)},
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    _cancel_flags.pop(run_id, None)
