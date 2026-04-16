"""
OAuth and connection management routes.

Routes
------
GET /v1/oauth/xero/authorize
    Begin the Xero OAuth flow for a given connection_id.
    Returns the authorization URL for the caller to open in a browser.
    Protected by INTERNAL_API_KEY.

GET /v1/oauth/xero/callback
    Xero redirects here with code + state after user consent.
    This endpoint is intentionally NOT protected by the API key because
    Xero (the redirect destination) cannot supply it.
    CSRF protection is provided by the state parameter.

POST /v1/oauth/ms/device-code/initiate
    Start a Microsoft device code flow for a given connection_id.
    Returns user_code, verification_uri, device_code, interval, expires_in.
    The operator visits verification_uri and enters user_code in a browser.
    Protected by INTERNAL_API_KEY.

POST /v1/oauth/ms/device-code/poll
    Poll for a completed Microsoft device code authorization.
    Pass the device_code received from the initiate endpoint.
    Returns 200 on success, 202 while pending, 410 on expiry.
    Protected by INTERNAL_API_KEY.

GET /v1/connections/{connection_id}/status
    Return validity state of any stored connection (Xero or MS).
    Protected by INTERNAL_API_KEY.

DELETE /v1/connections/{connection_id}
    Revoke and delete a stored connection.
    For Xero, also calls the Xero token revocation endpoint.
    Protected by INTERNAL_API_KEY.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.adapters.inbound.api.dependencies import (
    get_build_xero_authorization_url,
    get_get_connection_status,
    get_handle_xero_oauth_callback,
    get_initiate_ms_device_code,
    get_poll_ms_device_code,
    get_revoke_ms_connection,
    get_revoke_xero_connection,
)
from app.adapters.inbound.api.middleware import verify_api_key
from app.core.use_cases.oauth import (
    BuildXeroAuthorizationUrl,
    GetConnectionStatus,
    HandleXeroOAuthCallback,
    InitiateMSDeviceCodeFlow,
    PollMSDeviceCodeFlow,
    RevokeConnection,
)

router = APIRouter(tags=["oauth"])


# ── Response models ────────────────────────────────────────────────────────────


class AuthorizeResponse(BaseModel):
    authorization_url: str
    state: str


class ConnectionStatusResponse(BaseModel):
    connection_id: str
    status: str


class DeviceCodeInitiateResponse(BaseModel):
    """Returned when a device code flow is started.

    The operator must visit ``verification_uri`` and enter ``user_code``.
    Pass ``device_code`` and ``connection_id`` to the /poll endpoint.
    """

    connection_id: str
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    message: str


class DeviceCodePollRequest(BaseModel):
    connection_id: str
    device_code: str


class DeviceCodePollResponse(BaseModel):
    connection_id: str
    status: str  # "authorized" | "pending" | "expired"


# ── Xero OAuth flow ────────────────────────────────────────────────────────────


@router.get(
    "/v1/oauth/xero/authorize",
    response_model=AuthorizeResponse,
    dependencies=[Depends(verify_api_key)],
)
async def xero_authorize(
    connection_id: str = Query(
        ...,
        description="Logical name for this Xero connection (e.g. 'xero-acme').",
    ),
    use_case: BuildXeroAuthorizationUrl = Depends(get_build_xero_authorization_url),
) -> AuthorizeResponse:
    """Generate the Xero OAuth authorization URL.

    The caller (admin / setup tool) opens this URL in a browser.  After the
    user consents, Xero redirects to the /callback endpoint.
    """
    result = await use_case.execute(connection_id=connection_id)
    return AuthorizeResponse(
        authorization_url=result.authorization_url,
        state=result.state,
    )


@router.get("/v1/oauth/xero/callback")
async def xero_callback(
    code: str = Query(...),
    state: str = Query(...),
    use_case: HandleXeroOAuthCallback = Depends(get_handle_xero_oauth_callback),
) -> JSONResponse:
    """Handle the Xero OAuth redirect.

    Xero posts code + state as query parameters.  The state is verified
    against the stored value (CSRF protection); the code is exchanged for
    tokens which are persisted in Redis.

    Returns a simple JSON acknowledgment.  In production, you may redirect
    the browser to a success page instead, but returning JSON keeps the
    callback machine-friendly for testing.
    """
    connection_id = await use_case.execute(code=code, state=state)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "connection_id": connection_id,
            "detail": "Xero authorization complete. Token stored.",
        },
    )


# ── Microsoft device code flow ─────────────────────────────────────────────────


@router.post(
    "/v1/oauth/ms/device-code/initiate",
    response_model=DeviceCodeInitiateResponse,
    dependencies=[Depends(verify_api_key)],
)
async def ms_device_code_initiate(
    connection_id: str = Query(
        ...,
        description="Logical name for this MS connection (e.g. 'ms-default').",
    ),
    use_case: InitiateMSDeviceCodeFlow = Depends(get_initiate_ms_device_code),
) -> DeviceCodeInitiateResponse:
    """Start the Microsoft device code flow.

    Returns a ``user_code`` and ``verification_uri`` the operator must open in
    a browser to authorize the connection.  Store the returned ``device_code``
    and call /poll until the status is ``authorized``.
    """
    result = await use_case.execute()
    return DeviceCodeInitiateResponse(
        connection_id=connection_id,
        device_code=result.device_code,
        user_code=result.user_code,
        verification_uri=result.verification_uri,
        expires_in=result.expires_in,
        interval=result.interval,
        message=result.message,
    )


@router.post(
    "/v1/oauth/ms/device-code/poll",
    response_model=DeviceCodePollResponse,
    dependencies=[Depends(verify_api_key)],
)
async def ms_device_code_poll(
    body: DeviceCodePollRequest,
    use_case: PollMSDeviceCodeFlow = Depends(get_poll_ms_device_code),
) -> DeviceCodePollResponse:
    """Poll for a completed Microsoft device code authorization.

    Call this endpoint repeatedly at the ``interval`` returned by /initiate
    until the response status is ``authorized``.

    Status values:
      ``authorized`` — the operator completed sign-in; token is stored.
      ``pending``    — the operator has not yet completed sign-in; try again.
      ``expired``    — the device code window elapsed; start a new flow.
    """
    from app.adapters.outbound.ms_graph.device_code_client import (
        DeviceCodeExpired,
        DeviceCodePending,
    )

    try:
        connection_id = await use_case.execute(
            connection_id=body.connection_id,
            device_code=body.device_code,
        )
        return DeviceCodePollResponse(connection_id=connection_id, status="authorized")
    except DeviceCodePending:
        return DeviceCodePollResponse(
            connection_id=body.connection_id, status="pending"
        )
    except DeviceCodeExpired:
        return DeviceCodePollResponse(
            connection_id=body.connection_id, status="expired"
        )


# ── Connection management ──────────────────────────────────────────────────────


@router.get(
    "/v1/connections/{connection_id}/status",
    response_model=ConnectionStatusResponse,
    dependencies=[Depends(verify_api_key)],
)
async def get_connection_status(
    connection_id: str,
    use_case: GetConnectionStatus = Depends(get_get_connection_status),
) -> ConnectionStatusResponse:
    """Return the current token validity status for any connection.

    Possible values: valid, expired, missing.
    """
    result = await use_case.execute(connection_id=connection_id)
    return ConnectionStatusResponse(connection_id=connection_id, status=result.status)


@router.delete(
    "/v1/connections/{connection_id}/xero",
    status_code=204,
    dependencies=[Depends(verify_api_key)],
)
async def revoke_xero_connection(
    connection_id: str,
    use_case: RevokeConnection = Depends(get_revoke_xero_connection),
) -> None:
    """Revoke and delete a Xero connection.

    Calls the Xero token revocation endpoint (best-effort) then removes the
    token from Redis.  Returns 204 No Content on success.
    """
    await use_case.execute(connection_id=connection_id)


@router.delete(
    "/v1/connections/{connection_id}/ms",
    status_code=204,
    dependencies=[Depends(verify_api_key)],
)
async def revoke_ms_connection(
    connection_id: str,
    use_case: RevokeConnection = Depends(get_revoke_ms_connection),
) -> None:
    """Delete a Microsoft connection's cached token from Redis.

    Microsoft client_credentials tokens are not user-bound so no provider-
    level revocation is needed.  The token will be re-acquired automatically
    on the next Teams call.  Returns 204 No Content on success.
    """
    await use_case.execute(connection_id=connection_id)
