from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class StepType(str, enum.Enum):
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    SUB_AGENT = "sub_agent"


class StepStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        # Enforced at the DB level, not just checked-then-inserted in the
        # service layer: two concurrent POST /runs with the same
        # Idempotency-Key (the exact case idempotency keys exist to guard
        # against - a client retrying after a timeout while the first
        # request is still in flight) would otherwise both pass the
        # "does this key already exist" check and each insert their own
        # run. SQLite/Postgres both treat NULL as distinct from other NULLs
        # in a unique index, so requests without a key are unaffected.
        UniqueConstraint("agent", "idempotency_key", name="uq_runs_agent_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent: Mapped[str] = mapped_column(String(120), index=True)
    api_version: Mapped[str] = mapped_column(String(10), default="v1")
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED.value, index=True)

    input: Mapped[dict] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    seed: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    total_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    steps: Mapped[list["StepRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StepRecord.seq"
    )


class StepRecord(Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)

    type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default=StepStatus.RUNNING.value)

    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)

    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    span_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="steps")
