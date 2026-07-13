from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import init_db
from app.main import app


@pytest.fixture
async def client():
    # httpx's ASGITransport doesn't fire FastAPI lifespan events, so
    # initialize the schema explicitly instead of relying on app startup.
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_create_and_get_run(client: AsyncClient):
    res = await client.post(
        "/v1/runs",
        json={"agent": "test-agent", "input": {"query": "hello"}, "seed": 42},
    )
    assert res.status_code == 202
    run = res.json()
    assert run["status"] == "queued"
    assert run["agent"] == "test-agent"
    run_id = run["id"]

    # Poll until terminal (fake runner is fast under test speed factor).
    for _ in range(100):
        res = await client.get(f"/v1/runs/{run_id}")
        assert res.status_code == 200
        run = res.json()
        if run["status"] in ("succeeded", "failed", "cancelled"):
            break
        await asyncio.sleep(0.1)

    assert run["status"] in ("succeeded", "failed")
    assert run["trace_id"] is not None

    steps_res = await client.get(f"/v1/runs/{run_id}/steps")
    assert steps_res.status_code == 200
    assert len(steps_res.json()["data"]) > 0


@pytest.mark.asyncio
async def test_run_not_found(client: AsyncClient):
    res = await client.get("/v1/runs/run_doesnotexist")
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == "run_not_found"
    assert "request_id" in body


@pytest.mark.asyncio
async def test_idempotency_key_replay(client: AsyncClient):
    body = {"agent": "idempotent-agent", "input": {"x": 1}, "seed": 7}
    headers = {"Idempotency-Key": "test-key-123"}

    res1 = await client.post("/v1/runs", json=body, headers=headers)
    res2 = await client.post("/v1/runs", json=body, headers=headers)

    assert res1.status_code == 202
    assert res2.status_code == 202
    assert res1.json()["id"] == res2.json()["id"]
    assert res2.headers.get("Idempotency-Replayed") == "true"


@pytest.mark.asyncio
async def test_idempotency_key_conflict_on_different_body(client: AsyncClient):
    headers = {"Idempotency-Key": "conflict-key"}
    await client.post(
        "/v1/runs", json={"agent": "a", "input": {"x": 1}}, headers=headers
    )
    res = await client.post(
        "/v1/runs", json={"agent": "a", "input": {"x": 2}}, headers=headers
    )
    assert res.status_code == 409
    assert res.json()["code"] == "idempotency_key_conflict"


@pytest.mark.asyncio
async def test_list_runs_pagination(client: AsyncClient):
    for i in range(3):
        await client.post("/v1/runs", json={"agent": "list-test", "input": {"i": i}})

    res = await client.get("/v1/runs?limit=2&agent=list-test")
    assert res.status_code == 200
    body = res.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"] is not None


@pytest.mark.asyncio
async def test_analytics_summary_shape(client: AsyncClient):
    res = await client.get("/v1/analytics/summary")
    assert res.status_code == 200
    body = res.json()
    for key in ("run_count", "success_rate", "total_cost_usd", "cost_by_agent"):
        assert key in body
