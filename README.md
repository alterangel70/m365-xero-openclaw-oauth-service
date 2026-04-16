# M365 Xero OpenClaw Integration Service

A FastAPI microservice that allows OpenClaw to:
- Post messages and Adaptive Card approval requests to Microsoft Teams channels
- Create, submit, void, and query invoices in Xero

All provider tokens are managed internally. OpenClaw interacts with this service only via a static API key; it never handles OAuth flows or provider tokens directly.

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
| `GET` | `/v1/oauth/xero/authorize` | Begin Xero OAuth flow (returns redirect URL) |
| `GET` | `/v1/oauth/xero/callback` | Xero OAuth redirect target (no API key) |
| `GET` | `/v1/connections/{id}/status` | Token validity for a connection |
| `DELETE` | `/v1/connections/{id}/xero` | Revoke and delete a Xero connection |
| `DELETE` | `/v1/connections/{id}/ms` | Delete a Microsoft connection's cached token |

Interactive API docs are available at `http://localhost:8000/docs`.

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
