"""
Integration tests: Xero invoice lifecycle endpoints.

Real Redis (token pre-seeded), mock HTTP transport (Xero API).
"""

import httpx
import pytest

from tests.integration.conftest import (
    TEST_XERO_ACCESS_TOKEN,
    TEST_XERO_TENANT_ID,
    register_xero_token_in_redis,
)

CONNECTION_ID = "xero-test-conn"
_XERO_INVOICES_URL = "https://api.xero.com/api.xro/2.0/Invoices"


def _invoice_response(
    invoice_id: str = "inv-001",
    status: str = "DRAFT",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "Invoices": [
                {
                    "InvoiceID": invoice_id,
                    "Status": status,
                    "Type": "ACCREC",
                }
            ]
        },
    )


_CREATE_PAYLOAD = {
    "connection_id": CONNECTION_ID,
    "contact_id": "contact-uuid-abc",
    "line_items": [
        {
            "description": "Consulting services",
            "quantity": "2.0",
            "unit_amount": "500.00",
            "account_code": "200",
        }
    ],
    "due_date": "2026-05-31",
    "currency_code": "AUD",
    "reference": "REF-001",
}


# ── Tests: POST /v1/xero/invoices ─────────────────────────────────────────────


async def test_create_invoice_returns_201(app_client, mock_router, redis_client):
    """Happy path: valid token in Redis, Xero returns 200, app returns 201."""
    await register_xero_token_in_redis(redis_client, CONNECTION_ID)

    mock_router.post(_XERO_INVOICES_URL).mock(
        return_value=_invoice_response("inv-int-001", "DRAFT")
    )

    resp = await app_client.post(
        "/v1/xero/invoices",
        json=_CREATE_PAYLOAD,
        headers={"Idempotency-Key": "create-inv-1"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["invoice_id"] == "inv-int-001"
    assert body["status"] == "DRAFT"


async def test_create_invoice_idempotency_caches_result(
    app_client, mock_router, redis_client
):
    """Duplicate request with same Idempotency-Key must not call Xero twice."""
    await register_xero_token_in_redis(redis_client, CONNECTION_ID)

    xero_route = mock_router.post(_XERO_INVOICES_URL).mock(
        return_value=_invoice_response("inv-idem-001", "DRAFT")
    )

    headers = {"Idempotency-Key": "create-idem-key-1"}

    r1 = await app_client.post("/v1/xero/invoices", json=_CREATE_PAYLOAD, headers=headers)
    r2 = await app_client.post("/v1/xero/invoices", json=_CREATE_PAYLOAD, headers=headers)

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["invoice_id"] == r2.json()["invoice_id"] == "inv-idem-001"
    assert xero_route.call_count == 1


async def test_create_invoice_missing_idempotency_key_returns_400(
    app_client, mock_router, redis_client
):
    """Missing Idempotency-Key raises HTTPException(400), not a validation error."""
    await register_xero_token_in_redis(redis_client, CONNECTION_ID)

    resp = await app_client.post(
        "/v1/xero/invoices",
        json=_CREATE_PAYLOAD,
        # no Idempotency-Key header
    )

    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"


async def test_create_invoice_no_token_returns_404(app_client, mock_router):
    """No stored token → ConnectionMissingError → 404."""
    # redis is empty (flushed by fixture); no token pre-seeded.
    resp = await app_client.post(
        "/v1/xero/invoices",
        json=_CREATE_PAYLOAD,
        headers={"Idempotency-Key": "create-no-token"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"] == "connection_missing"


# ── Tests: GET /v1/xero/invoices/{invoice_id} ─────────────────────────────────


async def test_get_invoice_returns_200(app_client, mock_router, redis_client):
    """GET invoice proxies Xero response and returns 200."""
    await register_xero_token_in_redis(redis_client, CONNECTION_ID)

    mock_router.get(
        url__regex=r"https://api\.xero\.com/api\.xro/2\.0/Invoices/.+",
    ).mock(return_value=_invoice_response("inv-get-001", "AUTHORISED"))

    resp = await app_client.get(
        "/v1/xero/invoices/inv-get-001",
        params={"connection_id": CONNECTION_ID},
    )

    assert resp.status_code == 200
    assert resp.json()["invoice_id"] == "inv-get-001"
    assert resp.json()["status"] == "AUTHORISED"


# ── Tests: POST /v1/xero/invoices/{id}/submit ─────────────────────────────────


async def test_submit_invoice_returns_200(app_client, mock_router, redis_client):
    """Submit transitions invoice to AUTHORISED."""
    await register_xero_token_in_redis(redis_client, CONNECTION_ID)

    mock_router.post(
        url__regex=r"https://api\.xero\.com/api\.xro/2\.0/Invoices/.+",
    ).mock(return_value=_invoice_response("inv-sub-001", "AUTHORISED"))

    resp = await app_client.post(
        "/v1/xero/invoices/inv-sub-001/submit",
        json={"connection_id": CONNECTION_ID},
        headers={"Idempotency-Key": "submit-inv-1"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "AUTHORISED"


async def test_submit_invoice_idempotency(app_client, mock_router, redis_client):
    """Duplicate submit must not call Xero twice."""
    await register_xero_token_in_redis(redis_client, CONNECTION_ID)

    xero_route = mock_router.post(
        url__regex=r"https://api\.xero\.com/api\.xro/2\.0/Invoices/.+",
    ).mock(return_value=_invoice_response("inv-sub-idem", "AUTHORISED"))

    headers = {"Idempotency-Key": "submit-idem-1"}
    body = {"connection_id": CONNECTION_ID}

    r1 = await app_client.post(
        "/v1/xero/invoices/inv-sub-idem/submit", json=body, headers=headers
    )
    r2 = await app_client.post(
        "/v1/xero/invoices/inv-sub-idem/submit", json=body, headers=headers
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert xero_route.call_count == 1


# ── Tests: POST /v1/xero/invoices/{id}/void ───────────────────────────────────


async def test_void_invoice_returns_200(app_client, mock_router, redis_client):
    """Void transitions invoice to VOIDED."""
    await register_xero_token_in_redis(redis_client, CONNECTION_ID)

    mock_router.post(
        url__regex=r"https://api\.xero\.com/api\.xro/2\.0/Invoices/.+",
    ).mock(return_value=_invoice_response("inv-void-001", "VOIDED"))

    resp = await app_client.post(
        "/v1/xero/invoices/inv-void-001/void",
        json={"connection_id": CONNECTION_ID},
        headers={"Idempotency-Key": "void-inv-1"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "VOIDED"
