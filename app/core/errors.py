"""
Error contract for the public API.

Every non-2xx response is a `application/problem+json` body shaped like
RFC 7807, plus a `code` field for programmatic matching and a `request_id`
for support / trace correlation:

{
  "type": "https://docs.stackai.com/errors/run_not_found",
  "title": "Run not found",
  "status": 404,
  "detail": "No run with id 'run_abc123' exists.",
  "code": "run_not_found",
  "request_id": "req_9f2c..."
}

Callers should branch on `code`, not on `title`/`detail`, which are
human-readable and may change wording over time.
"""

from __future__ import annotations

import uuid

from fastapi import Request, status
from fastapi.responses import JSONResponse

ERROR_DOCS_BASE = "https://docs.stackai.com/errors"


class ApiError(Exception):
    def __init__(self, *, code: str, title: str, detail: str, status_code: int) -> None:
        self.code = code
        self.title = title
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class RunNotFoundError(ApiError):
    def __init__(self, run_id: str) -> None:
        super().__init__(
            code="run_not_found",
            title="Run not found",
            detail=f"No run with id '{run_id}' exists.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class InvalidRunStateError(ApiError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code="invalid_run_state",
            title="Run is not in a state that allows this operation",
            detail=detail,
            status_code=status.HTTP_409_CONFLICT,
        )


class IdempotencyKeyReuseError(ApiError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code="idempotency_key_conflict",
            title="Idempotency-Key reused with a different request body",
            detail=detail,
            status_code=status.HTTP_409_CONFLICT,
        )


def problem_response(request: Request, err: ApiError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=err.status_code,
        media_type="application/problem+json",
        content={
            "type": f"{ERROR_DOCS_BASE}/{err.code}",
            "title": err.title,
            "status": err.status_code,
            "detail": err.detail,
            "code": err.code,
            "request_id": request_id,
        },
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return problem_response(request, exc)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        media_type="application/problem+json",
        content={
            "type": f"{ERROR_DOCS_BASE}/internal_error",
            "title": "Internal server error",
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "detail": "Something went wrong on our end. Please retry; if it persists, "
            "contact support with the request_id below.",
            "code": "internal_error",
            "request_id": request_id,
        },
    )
