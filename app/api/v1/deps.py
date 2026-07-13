from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

__all__ = ["get_session", "get_idempotency_key"]


async def get_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")
) -> str | None:
    return idempotency_key
