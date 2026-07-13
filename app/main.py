from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.config import get_settings
from app.core.errors import ApiError, api_error_handler, unhandled_error_handler
from app.db.session import init_db
from app.telemetry import setup_telemetry

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="StackAI Agent Runs API",
    description=(
        "Start agent runs, follow their progress, and inspect what happened - "
        "which steps ran, how long they took, what they cost, and why they "
        "failed if they did. Every run is fully traced; see `trace_id` on the "
        "Run resource to jump straight into the observability backend."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Real OTel wiring - must run before routes are registered so FastAPI
# instrumentation can wrap them.
setup_telemetry(app)

app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-Id", f"req_{uuid.uuid4().hex[:16]}")
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


app.include_router(api_router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse("static/dashboard.html")


@app.get("/health", tags=["meta"], summary="Liveness check")
async def health() -> dict:
    return {"status": "ok", "service": settings.service_name}
