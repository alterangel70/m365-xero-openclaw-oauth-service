# M365 Xero OpenClaw Integration Service

A FastAPI microservice that allows OpenClaw to:
- Post messages and Adaptive Card approval requests to Microsoft Teams channels
- Create, submit, void, and query invoices in Xero

All provider tokens are managed internally. OpenClaw interacts with this service only via a static API key; it never handles OAuth flows or provider tokens directly.

**Microsoft** uses the OAuth 2.0 **Device Code Flow** (delegated, on behalf of a user). A one-time operator authorization is required; the service then auto-refreshes the token silently.

**Xero** uses OAuth 2.0 **Authorization Code** with rotating refresh tokens. A one-time operator browser-based consent is required per Xero organization.

---

## Technologies

| Layer | Library |
|---|---|
| Web framework | FastAPI + Uvicorn |
| HTTP client | httpx |
| OAuth (Xero) | Authlib |
| Token / state store | Redis (redis.asyncio) |
| Configuration | pydantic-settings |
| Structured logging | seqlog (Seq) |
| Test runner | pytest + pytest-asyncio |
| Unit test Redis mock | fakeredis |
| Integration test HTTP mock | respx |

Python 3.12, Docker, Redis 7.

---

## Run locally

### Prerequisites

- Docker and Docker Compose
- A `.env` file (copy from `.env.example` and fill in credentials)

### Start

```bash
docker compose up --build
```

The service is available at `http://localhost:8000`.
The Redis instance runs inside Docker on the `integration_net` bridge network and is not exposed to the host.

### Stop

```bash
docker compose down
```

---

## Main endpoints

All endpoints except `/health` and `GET /v1/oauth/xero/callback` require:

```
Authorization: Bearer <INTERNAL_API_KEY>
```

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness + dependency check (Redis, MS Graph) |
| `POST` | `/v1/teams/messages` | Send a text/HTML message to a Teams channel |
| `POST` | `/v1/teams/approvals` | Send an Adaptive Card approval to a Teams channel |
| `POST` | `/v1/xero/invoices` | Create a draft Xero invoice |
| `GET` | `/v1/xero/invoices/{id}` | Fetch a Xero invoice |
| `POST` | `/v1/xero/invoices/{id}/submit` | Transition invoice to AUTHORISED |
| `POST` | `/v1/xero/invoices/{id}/void` | Void an invoice |
| `GET` | `/v1/xero/contacts` | List Xero contacts (optional `?search=` filter) |
| `GET` | `/v1/oauth/xero/authorize` | Begin Xero OAuth flow (returns redirect URL) |
| `GET` | `/v1/oauth/xero/callback` | Xero OAuth redirect target (no API key) |
| `POST` | `/v1/oauth/ms/device-code/initiate` | Begin MS device code flow (returns user code + URL) |
| `POST` | `/v1/oauth/ms/device-code/poll` | Poll MS device code flow until authorized |
| `GET` | `/v1/connections/{id}/status` | Token validity for a connection |
| `DELETE` | `/v1/connections/{id}/xero` | Revoke and delete a Xero connection |
| `DELETE` | `/v1/connections/{id}/ms` | Delete a Microsoft connection's cached token |

Interactive API docs are available at `http://localhost:8000/docs`.

---

## One-time authorization setup

Both providers require a one-time operator authorization before the service can make API calls on their behalf. Tokens are stored in Redis and auto-refreshed from that point on.

Replace `<API_KEY>` with the value of `INTERNAL_API_KEY` from your `.env`.

### Microsoft — Device Code Flow

**1. Initiate the flow:**
```bash
curl -s -X POST \
  -H "Authorization: Bearer <API_KEY>" \
  "http://localhost:8080/v1/oauth/ms/device-code/initiate?connection_id=ms-default"
```

This returns a `user_code`, a `verification_uri`, and a `device_code`.

**2.** Open the `verification_uri` (e.g. `https://login.microsoft.com/device`) in a browser, enter the `user_code`, and sign in with the Azure account belonging to the configured tenant.

**3. Poll until authorized** (repeat every 5 seconds until `"status": "authorized"`):
```bash
curl -s -X POST \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"connection_id": "ms-default", "device_code": "<device_code from step 1>"}' \
  http://localhost:8080/v1/oauth/ms/device-code/poll
```

**4. Verify:**
```bash
curl -s -H "Authorization: Bearer <API_KEY>" \
  http://localhost:8080/v1/connections/ms-default/status
# → {"status":"valid"}
```

> The device code expires after 15 minutes. If it expires before you complete sign-in, restart from step 1.

---

### Xero — Authorization Code Flow

**1. Get the authorization URL:**
```bash
curl -s \
  -H "Authorization: Bearer <API_KEY>" \
  "http://localhost:8080/v1/oauth/xero/authorize?connection_id=xero-default"
```

This returns `{"authorization_url": "https://login.xero.com/...", "state": "..."}`.

**2.** Open the `authorization_url` in a browser and authorize the app in Xero.

**3.** Xero will redirect to the `XERO_REDIRECT_URI` (e.g. `http://localhost:8080/callback`). If your browser can't reach that address, it will show a connection error — that's expected. **Copy the full URL from the browser address bar.**

**4. Complete the callback manually** using the `code` and `state` from that URL:
```bash
curl -s "http://localhost:8080/v1/oauth/xero/callback?code=<CODE>&state=<STATE>"
# → {"status":"ok","connection_id":"xero-default"}
```

**5. Verify:**
```bash
curl -s -H "Authorization: Bearer <API_KEY>" \
  http://localhost:8080/v1/connections/xero-default/status
# → {"status":"valid"}
```

---

## Run tests

### Unit tests (no external services needed)

```bash
docker compose run --rm app sh -c "pip install -r requirements-test.txt && pytest tests/unit -v"
```

Or locally if Python 3.12 is installed:

```bash
pip install -r requirements.txt -r requirements-test.txt
pytest tests/unit -v
```

### Integration tests (real Redis via Docker)

```bash
docker compose -f docker-compose.test.yml run --rm integration-tests
```

This starts a disposable Redis container and runs `tests/integration/` against the full ASGI stack.

---

## Documentation

- [Architecture](docs/architecture.md) — hexagonal structure, flows, token lifecycle, idempotency
- [Operations](docs/operations.md) — environment variables, Docker, Seq logging, troubleshooting
