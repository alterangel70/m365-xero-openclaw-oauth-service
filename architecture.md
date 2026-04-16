# Architecture

## Overview

The service is structured as a hexagonal (ports-and-adapters) architecture. The domain core contains zero framework imports. All I/O — HTTP, Redis, provider APIs — lives in adapter modules that implement abstract ports defined by the core.

```
┌──────────────────────────────────────────────────────┐
│  Inbound adapters (HTTP)                             │
│  app/adapters/inbound/api/                           │
│    teams.py  xero.py  oauth.py  middleware.py        │
└────────────────────┬─────────────────────────────────┘
                     │  FastAPI dependency injection
                     ▼
┌──────────────────────────────────────────────────────┐
│  Core                                                │
│  app/core/                                           │
│    domain/     — value objects, no I/O               │
│    ports/      — abstract interfaces                 │
│    use_cases/  — business logic, calls only ports    │
│    errors.py   — domain exception hierarchy          │
└────────────────────┬─────────────────────────────────┘
                     │  constructor injection
                     ▼
┌──────────────────────────────────────────────────────┐
│  Outbound adapters                                   │
│  app/adapters/outbound/                              │
│    ms_graph/   — MS Graph HTTP client + token mgr   │
│    xero/       — Xero HTTP client + OAuth + token mgr│
│    token_store/— Redis token, idempotency, state     │
│    lock/       — distributed Redis lock              │
└──────────────────────────────────────────────────────┘
```

---

## Module structure

```
app/
├── main.py                          # FastAPI app, lifespan, error handlers
├── adapters/
│   ├── inbound/api/
│   │   ├── teams.py                 # POST /v1/teams/*
│   │   ├── xero.py                  # /v1/xero/*
│   │   ├── oauth.py                 # /v1/oauth/*, /v1/connections/*
│   │   ├── dependencies.py          # DI wiring (assembles use cases)
│   │   └── middleware.py            # API key auth, RequestIdMiddleware
│   └── outbound/
│       ├── ms_graph/
│       │   ├── client.py            # MSGraphClient (sends messages)
│       │   ├── token_manager.py     # MSTokenManager (device code / delegated)
│       │   ├── device_code_client.py# MSDeviceCodeClient (initiate + poll + refresh)
│       │   └── card_builder.py      # Builds Adaptive Card JSON
│       ├── xero/
│       │   ├── client.py            # XeroHttpClient (invoices)
│       │   ├── token_manager.py     # XeroTokenManager (refresh rotation)
│       │   └── oauth_client.py      # XeroAuthlibOAuthClient (auth code flow)
│       ├── token_store/
│       │   ├── redis_token_store.py       # token:{connection_id} hashes
│       │   ├── redis_idempotency_store.py # idempotency:{op}:{key} strings
│       │   └── redis_oauth_state_store.py # oauth:state:{state} strings
│       └── lock/
│           └── redis_lock.py        # Distributed SET NX / Lua-safe release
├── core/
│   ├── domain/
│   │   ├── token.py     # TokenSet (frozen dataclass)
│   │   ├── teams.py     # TeamsMessage, TeamsApprovalCard
│   │   ├── xero.py      # XeroInvoice, XeroLineItem
│   │   └── provider.py  # Provider enum
│   ├── ports/           # Abstract base classes (interfaces)
│   ├── use_cases/
│   │   ├── teams.py     # SendTeamsMessage, SendTeamsApprovalCard
│   │   ├── xero.py      # CreateXeroDraftInvoice, SubmitXeroInvoice, GetXeroInvoice, ListXeroContacts, ...
│   │   ├── oauth.py     # BuildXeroAuthorizationUrl, HandleXeroOAuthCallback, InitiateMSDeviceCodeFlow, PollMSDeviceCodeFlow, ...
│   │   └── results.py   # Result value objects
│   └── errors.py
└── infrastructure/
    ├── config.py        # pydantic-settings Settings, get_settings()
    ├── logging.py       # configure_logging(), flush_seq_handler(), request-ID helpers
    └── redis_client.py  # create_redis_pool()
```

---

## Domain / ports / adapters

### Domain

The domain layer (`app/core/domain/`) contains only immutable value objects and pure logic:

- **`TokenSet`** — frozen dataclass representing a provider token (access token, optional refresh token, expiry, tenant ID)
- **`TeamsMessage`** / **`TeamsApprovalCard`** — value objects for Teams payloads
- **`XeroInvoice`** / **`XeroLineItem`** — value objects for Xero invoice creation

### Ports

Abstract base classes in `app/core/ports/`:

| Port | Implemented by |
|---|---|
| `AbstractTokenStore` | `RedisTokenStore` |
| `AbstractIdempotencyStore` | `RedisIdempotencyStore` |
| `AbstractOAuthStateStore` | `RedisOAuthStateStore` |
| `AbstractLockManager` | `RedisLockManager` |
| `AbstractTeamsClient` | `MSGraphClient` |
| `AbstractXeroClient` | `XeroHttpClient` |
| `AbstractOAuthClient` | `XeroAuthlibOAuthClient` |

`MSDeviceCodeClient` is a concrete helper (not a port) used directly by `MSTokenManager` for device code initiation and refresh calls.

### Use cases

Use cases (`app/core/use_cases/`) contain business logic. They call only port interfaces; they never import FastAPI, httpx, Redis, or any adapter. FastAPI's dependency injection system assembles the concrete collaborators via `app/adapters/inbound/api/dependencies.py`.

---

## Microsoft flow

Microsoft uses the OAuth 2.0 **Device Code Flow** (delegated, on behalf of a user). A one-time operator authorization is required; the stored refresh token is then used to silently renew access tokens.

### One-time setup

```
Operator → POST /v1/oauth/ms/device-code/initiate?connection_id=ms-default
               ↓
           MSDeviceCodeClient.start_device_code_flow()
               POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode
           Returns { user_code, verification_uri, device_code, expires_in, interval }
               ↓
           Operator opens verification_uri in browser, enters user_code, signs in
               ↓
           Operator → POST /v1/oauth/ms/device-code/poll  { connection_id, device_code }
               ↓
           MSDeviceCodeClient.poll_device_code(device_code)
               POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
               (grant_type=urn:ietf:params:oauth:grant-type:device_code)
           On success: access_token + refresh_token stored in Redis token:{connection_id}
```

Poll returns `{ status: "pending" | "authorized" | "expired" }`. Keep polling at the specified interval until `authorized`.

### Subsequent API calls
              ↓
          SendTeamsMessage use case
              ↓
          MSGraphClient._post_to_channel()
              ↓
          MSTokenManager.get_token(connection_id)
              ├─ Load from Redis: token:{connection_id}
              │   ├─ If missing → raise ConnectionMissingError (operator must complete device code flow)
              │   └─ If valid → return access_token
              └─ Acquire distributed lock
                  ├─ Double-check Redis (another worker may have refreshed)
                  └─ MSDeviceCodeClient.refresh_token(current_token)
                      POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
                          (grant_type=refresh_token)
                      Store result in Redis
              ↓
          POST https://graph.microsoft.com/v1.0/teams/{id}/channels/{id}/messages
              ↓
          Return message_id → OpenClaw
```

On a 401 from MS Graph, the client forces a token refresh and retries exactly once.

If no token is present in Redis (device code flow not yet completed), `ConnectionMissingError` is raised and the caller receives a 404.

---

## Xero flow

Xero uses OAuth 2.0 **authorization_code** with rotating refresh tokens.

### One-time setup (run once per Xero organization)

```
OpenClaw → GET /v1/oauth/xero/authorize?connection_id=xero-prod
               ↓
           Generates random state, stores oauth:state:{state} → connection_id in Redis (TTL 600s)
           Returns { authorization_url, state }
               ↓
           Admin opens authorization_url in browser → Xero consent screen
               ↓
           Xero redirects → GET /v1/oauth/xero/callback?code=...&state=...
               ↓
           State popped from Redis (atomic, one-time)
           Code exchanged for token via Authlib
           GET https://api.xero.com/connections → retrieve tenant ID
           Token stored in Redis: token:{connection_id}
```

### Subsequent API calls

```
OpenClaw → POST /v1/xero/invoices
               ↓
           CreateXeroDraftInvoice use case
               ↓
           XeroHttpClient._request()
               ↓
           XeroTokenManager.get_valid_token(connection_id)
               ├─ Load token:{connection_id} from Redis
               │   └─ If valid → return TokenSet
               └─ Acquire distributed lock
                   ├─ Double-check Redis
                   └─ POST https://identity.xero.com/connect/token (refresh_token grant)
                       Store NEW token set in Redis (new access + new refresh token)
               ↓
           POST https://api.xero.com/api.xro/2.0/Invoices
           with Authorization + Xero-tenant-ID headers
```

---

## Token lifecycle

### Storage

All tokens are stored in Redis as hashes under `token:{connection_id}`:

```
token:xero-prod
  access_token   = "eyJ..."
  refresh_token  = "aB3..."
  expires_at     = "2026-04-16T14:30:00+00:00"
  token_type     = "Bearer"
  scope          = "accounting.transactions ..."
  xero_tenant_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Microsoft tokens have a `refresh_token` (mandatory after device code flow) and empty `xero_tenant_id`.

No TTL is set on the Redis key; expiry is determined by comparing `expires_at` to the current time minus `REFRESH_BUFFER_SECONDS`.

### Refresh logic

Both `MSTokenManager` and `XeroTokenManager` implement the same double-checked locking pattern:

1. Load token from Redis. If valid (not within `refresh_buffer_seconds` of expiry), return immediately.
2. Acquire a distributed Redis lock (`lock:{connection_id}`) using `SET NX EX`.
3. **Re-load** from Redis inside the lock — another worker may have already refreshed.
4. If still stale, call the provider. Store the new token before releasing the lock.
5. Release the lock via a Lua-safe atomic conditional delete.

Contention: if the lock is held, wait 100 ms and retry up to 5 times before raising `LockTimeoutError`.

Xero-specific: the new refresh token returned by the provider must be persisted before the lock is released, because Xero immediately invalidates the old refresh token.

---

## Redis stores and keys

| Key pattern | Type | Content | TTL |
|---|---|---|---|
| `token:{connection_id}` | Hash | Provider TokenSet (all fields) | None (app-managed) |
| `lock:{connection_id}` | String | UUID owner token | `LOCK_TTL_SECONDS` (30s) |
| `idempotency:{op}:{key}` | String | JSON-serialized result | `IDEMPOTENCY_TTL_SECONDS` (24h) |
| `oauth:state:{state}` | String | `connection_id` | `OAUTH_STATE_TTL_SECONDS` (600s) |

---

## Idempotency

Write operations accept an optional `Idempotency-Key` request header. Xero invoice creation, submission, and voiding require it; Teams messages accept it optionally.

Flow:

1. Use case constructs a namespaced Redis key: `idempotency:{operation}:{idempotency_key}`
2. If a result is cached in `RedisIdempotencyStore`, return it immediately without calling the provider.
3. Otherwise, call the provider, then persist the result with a 24-hour TTL.

The idempotency key is scoped to the operation name, so the same key value used for `create_invoice` and `send_teams_message` does not collide.

---

## How OpenClaw interacts with this service

OpenClaw treats this service as a thin integration broker:

1. **Teams messages** — OpenClaw POSTs to `/v1/teams/messages` or `/v1/teams/approvals` with team/channel IDs and content. The service handles token acquisition and the MS Graph API call.

2. **Xero invoices** — OpenClaw POSTs to `/v1/xero/invoices` with invoice data. It supplies an `Idempotency-Key` to prevent duplicates on retries. The service handles token refresh, tenant ID headers, and the Xero API call.

3. **Xero contacts** — OpenClaw (or an operator) calls `GET /v1/xero/contacts?connection_id=...` optionally with a `?search=` query to locate a contact ID before creating an invoice.

4. **Xero authorization (one-time setup)** — An operator calls `/v1/oauth/xero/authorize`, opens the returned URL in a browser, and completes the Xero consent screen. The callback stores the token; OpenClaw's subsequent invoice calls work transparently from that point on.

5. **Microsoft authorization (one-time setup)** — An operator calls `POST /v1/oauth/ms/device-code/initiate`, opens the returned `verification_uri` in a browser, enters the `user_code`, and signs in. The operator then polls `POST /v1/oauth/ms/device-code/poll` until `status: authorized`. Subsequent Teams calls work autonomously via refresh token.

6. **Connection health** — OpenClaw can call `/v1/connections/{id}/status` to check whether a connection's token is `valid`, `expired`, or `missing` before attempting operations.

OpenClaw never sees raw OAuth tokens; it authenticates to this service only with the static `INTERNAL_API_KEY`.
