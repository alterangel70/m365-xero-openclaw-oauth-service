"""
FastAPI dependency injection wiring.

Each function constructs one collaborator from its dependencies.
All state lives either in app.state (Redis pool, httpx client) or is
instantiated fresh per request (lightweight wrappers with no internal state).

Nothing in this module reaches into provider adapters directly; it only
assembles already-implemented classes and passes them through the DI chain.
"""

from fastapi import Depends, Request

from app.adapters.outbound.lock.redis_lock import RedisLockManager
from app.adapters.outbound.ms_graph.client import MSGraphClient
from app.adapters.outbound.ms_graph.device_code_client import MSDeviceCodeClient
from app.adapters.outbound.ms_graph.token_manager import MSTokenManager
from app.adapters.outbound.token_store.redis_idempotency_store import RedisIdempotencyStore
from app.adapters.outbound.token_store.redis_token_store import RedisTokenStore
from app.adapters.outbound.xero.client import XeroHttpClient
from app.adapters.outbound.token_store.redis_oauth_state_store import RedisOAuthStateStore
from app.adapters.outbound.xero.oauth_client import XeroAuthlibOAuthClient
from app.adapters.outbound.xero.token_manager import XeroTokenManager
from app.core.use_cases.oauth import (
    BuildXeroAuthorizationUrl,
    GetConnectionStatus,
    HandleXeroOAuthCallback,
    InitiateMSDeviceCodeFlow,
    PollMSDeviceCodeFlow,
    RevokeConnection,
)
from app.core.use_cases.teams import SendTeamsApprovalCard, SendTeamsMessage
from app.core.use_cases.xero import (
    CreateXeroDraftInvoice,
    GetXeroInvoice,
    ListXeroContacts,
    SubmitXeroInvoice,
    VoidXeroInvoice,
)
from app.infrastructure.config import Settings, get_settings


# ── Shared infrastructure ─────────────────────────────────────────────────────


def get_redis(request: Request):
    """Return the async Redis client stored on app.state by the lifespan."""
    return request.app.state.redis


def get_http_client(request: Request):
    """Return the shared httpx.AsyncClient stored on app.state by the lifespan."""
    return request.app.state.http_client


# ── Redis-backed adapters ─────────────────────────────────────────────────────


def get_token_store(redis=Depends(get_redis)) -> RedisTokenStore:
    return RedisTokenStore(redis)


def get_lock_manager(redis=Depends(get_redis)) -> RedisLockManager:
    return RedisLockManager(redis)


def get_idempotency_store(redis=Depends(get_redis)) -> RedisIdempotencyStore:
    return RedisIdempotencyStore(redis)


# ── Microsoft Graph ───────────────────────────────────────────────────────────


def get_ms_device_code_client(
    http_client=Depends(get_http_client),
    settings: Settings = Depends(get_settings),
) -> MSDeviceCodeClient:
    return MSDeviceCodeClient(
        http_client=http_client,
        tenant_id=settings.ms_tenant_id,
        client_id=settings.ms_client_id,
        scopes=settings.ms_graph_scopes,
    )


def get_ms_token_manager(
    token_store: RedisTokenStore = Depends(get_token_store),
    lock_manager: RedisLockManager = Depends(get_lock_manager),
    device_code_client: MSDeviceCodeClient = Depends(get_ms_device_code_client),
    settings: Settings = Depends(get_settings),
) -> MSTokenManager:
    return MSTokenManager(
        token_store=token_store,
        lock_manager=lock_manager,
        device_code_client=device_code_client,
        refresh_buffer_seconds=settings.refresh_buffer_seconds,
    )


def get_teams_client(
    token_manager: MSTokenManager = Depends(get_ms_token_manager),
    http_client=Depends(get_http_client),
) -> MSGraphClient:
    return MSGraphClient(token_manager=token_manager, http_client=http_client)


# ── Teams use cases ───────────────────────────────────────────────────────────


def get_send_teams_message(
    teams_client: MSGraphClient = Depends(get_teams_client),
    idempotency_store: RedisIdempotencyStore = Depends(get_idempotency_store),
    settings: Settings = Depends(get_settings),
) -> SendTeamsMessage:
    return SendTeamsMessage(
        teams_client=teams_client,
        idempotency_store=idempotency_store,
        idempotency_ttl_seconds=settings.idempotency_ttl_seconds,
    )


def get_send_teams_approval_card(
    teams_client: MSGraphClient = Depends(get_teams_client),
    idempotency_store: RedisIdempotencyStore = Depends(get_idempotency_store),
    settings: Settings = Depends(get_settings),
) -> SendTeamsApprovalCard:
    return SendTeamsApprovalCard(
        teams_client=teams_client,
        idempotency_store=idempotency_store,
        idempotency_ttl_seconds=settings.idempotency_ttl_seconds,
    )


# ── Xero ──────────────────────────────────────────────────────────────────────


def get_xero_oauth_client(
    http_client=Depends(get_http_client),
    settings: Settings = Depends(get_settings),
) -> XeroAuthlibOAuthClient:
    return XeroAuthlibOAuthClient(
        client_id=settings.xero_client_id,
        client_secret=settings.xero_client_secret,
        redirect_uri=settings.xero_redirect_uri,
        scopes=settings.xero_scopes,
        http_client=http_client,
    )


def get_xero_token_manager(
    token_store: RedisTokenStore = Depends(get_token_store),
    lock_manager: RedisLockManager = Depends(get_lock_manager),
    oauth_client: XeroAuthlibOAuthClient = Depends(get_xero_oauth_client),
    settings: Settings = Depends(get_settings),
) -> XeroTokenManager:
    return XeroTokenManager(
        token_store=token_store,
        lock_manager=lock_manager,
        oauth_client=oauth_client,
        refresh_buffer_seconds=settings.refresh_buffer_seconds,
    )


def get_xero_client(
    token_manager: XeroTokenManager = Depends(get_xero_token_manager),
    http_client=Depends(get_http_client),
) -> XeroHttpClient:
    return XeroHttpClient(token_manager=token_manager, http_client=http_client)


# ── Xero use cases ────────────────────────────────────────────────────────────


def get_create_xero_draft_invoice(
    xero_client: XeroHttpClient = Depends(get_xero_client),
    idempotency_store: RedisIdempotencyStore = Depends(get_idempotency_store),
    settings: Settings = Depends(get_settings),
) -> CreateXeroDraftInvoice:
    return CreateXeroDraftInvoice(
        xero_client=xero_client,
        idempotency_store=idempotency_store,
        idempotency_ttl_seconds=settings.idempotency_ttl_seconds,
    )


def get_submit_xero_invoice(
    xero_client: XeroHttpClient = Depends(get_xero_client),
    idempotency_store: RedisIdempotencyStore = Depends(get_idempotency_store),
    settings: Settings = Depends(get_settings),
) -> SubmitXeroInvoice:
    return SubmitXeroInvoice(
        xero_client=xero_client,
        idempotency_store=idempotency_store,
        idempotency_ttl_seconds=settings.idempotency_ttl_seconds,
    )


def get_get_xero_invoice(
    xero_client: XeroHttpClient = Depends(get_xero_client),
) -> GetXeroInvoice:
    return GetXeroInvoice(xero_client=xero_client)


def get_list_xero_contacts(
    xero_client: XeroHttpClient = Depends(get_xero_client),
) -> ListXeroContacts:
    return ListXeroContacts(xero_client=xero_client)


def get_void_xero_invoice(
    xero_client: XeroHttpClient = Depends(get_xero_client),
    idempotency_store: RedisIdempotencyStore = Depends(get_idempotency_store),
    settings: Settings = Depends(get_settings),
) -> VoidXeroInvoice:
    return VoidXeroInvoice(
        xero_client=xero_client,
        idempotency_store=idempotency_store,
        idempotency_ttl_seconds=settings.idempotency_ttl_seconds,
    )


# ── OAuth state store ─────────────────────────────────────────────────────────


def get_oauth_state_store(redis=Depends(get_redis)) -> RedisOAuthStateStore:
    return RedisOAuthStateStore(redis)


# ── OAuth use cases ────────────────────────────────────────────────────────────


def get_build_xero_authorization_url(
    oauth_client: XeroAuthlibOAuthClient = Depends(get_xero_oauth_client),
    state_store: RedisOAuthStateStore = Depends(get_oauth_state_store),
    settings: Settings = Depends(get_settings),
) -> BuildXeroAuthorizationUrl:
    return BuildXeroAuthorizationUrl(
        oauth_client=oauth_client,
        state_store=state_store,
        oauth_state_ttl_seconds=settings.oauth_state_ttl_seconds,
    )


def get_handle_xero_oauth_callback(
    oauth_client: XeroAuthlibOAuthClient = Depends(get_xero_oauth_client),
    state_store: RedisOAuthStateStore = Depends(get_oauth_state_store),
    token_store: RedisTokenStore = Depends(get_token_store),
) -> HandleXeroOAuthCallback:
    return HandleXeroOAuthCallback(
        oauth_client=oauth_client,
        state_store=state_store,
        token_store=token_store,
    )


def get_get_connection_status(
    token_store: RedisTokenStore = Depends(get_token_store),
    settings: Settings = Depends(get_settings),
) -> GetConnectionStatus:
    return GetConnectionStatus(
        token_store=token_store,
        refresh_buffer_seconds=settings.refresh_buffer_seconds,
    )


def get_revoke_xero_connection(
    token_store: RedisTokenStore = Depends(get_token_store),
    oauth_client: XeroAuthlibOAuthClient = Depends(get_xero_oauth_client),
) -> RevokeConnection:
    """RevokeConnection for Xero — calls the provider revocation endpoint."""
    return RevokeConnection(token_store=token_store, oauth_client=oauth_client)


def get_revoke_ms_connection(
    token_store: RedisTokenStore = Depends(get_token_store),
) -> RevokeConnection:
    """RevokeConnection for Microsoft — no provider revocation (delegated token; revoke locally only)."""
    return RevokeConnection(token_store=token_store, oauth_client=None)


def get_initiate_ms_device_code(
    device_code_client: MSDeviceCodeClient = Depends(get_ms_device_code_client),
) -> InitiateMSDeviceCodeFlow:
    return InitiateMSDeviceCodeFlow(ms_device_code_client=device_code_client)


def get_poll_ms_device_code(
    device_code_client: MSDeviceCodeClient = Depends(get_ms_device_code_client),
    token_store: RedisTokenStore = Depends(get_token_store),
) -> PollMSDeviceCodeFlow:
    return PollMSDeviceCodeFlow(
        ms_device_code_client=device_code_client,
        token_store=token_store,
    )
