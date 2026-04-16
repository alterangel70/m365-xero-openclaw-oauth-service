"""
Unit tests for RequestIdMiddleware.

Uses a minimal in-process FastAPI app with no external dependencies.
"""

import pytest
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.adapters.inbound.api.middleware import RequestIdMiddleware


# ── Minimal test application ──────────────────────────────────────────────────


def _make_app() -> FastAPI:
    mini = FastAPI()
    mini.add_middleware(RequestIdMiddleware)

    @mini.get("/echo")
    async def echo(request: Request) -> JSONResponse:
        return JSONResponse({"request_id": request.state.request_id})

    return mini


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app()),
        base_url="http://test",
    ) as c:
        yield c


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_generates_request_id_when_none_provided(client):
    """No inbound X-Request-ID → middleware generates a non-empty UUID."""
    resp = await client.get("/echo")

    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID", "")
    assert len(rid) == 36  # standard UUID4 string length


async def test_echoes_request_id_in_response_header(client):
    """The generated ID appears in both the header and request.state."""
    resp = await client.get("/echo")

    assert resp.headers["X-Request-ID"] == resp.json()["request_id"]


async def test_passes_through_caller_request_id(client):
    """Caller-supplied X-Request-ID is preserved verbatim."""
    caller_id = "my-trace-id-abc-123"
    resp = await client.get("/echo", headers={"X-Request-ID": caller_id})

    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == caller_id
    assert resp.json()["request_id"] == caller_id


async def test_different_requests_receive_different_ids(client):
    """Each request gets a distinct correlation ID."""
    r1 = await client.get("/echo")
    r2 = await client.get("/echo")

    assert r1.headers["X-Request-ID"] != r2.headers["X-Request-ID"]


async def test_x_request_id_header_always_present(client):
    """X-Request-ID is present on every response regardless of status."""
    resp = await client.get("/echo")

    assert "X-Request-ID" in resp.headers
