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
    get_revoke_xero_connection,
    get_revoke_ms_connection,
)
from app.adapters.inbound.api.middleware import verify_api_key
from app.core.use_cases.oauth import (
    BuildXeroAuthorizationUrl,
    GetConnectionStatus,
    HandleXeroOAuthCallback,
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
