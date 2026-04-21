# Data Reference — openclaw-m365-xero-integration-service

Referencia de configuración, tiempos de vida y estructuras de datos en Redis.

---

## Configuración relevante (.env / Settings)

| Variable | Default | Descripción |
|---|---|---|
| `REFRESH_BUFFER_SECONDS` | `300` | Segundos antes de la expiración en que se hace refresh proactivo del token |
| `OAUTH_STATE_TTL_SECONDS` | `600` | Duración del estado CSRF durante el OAuth dance de Xero (10 min) |
| `IDEMPOTENCY_TTL_SECONDS` | `86400` | Caché de resultados de operaciones write (24 h) |
| `REDIS_URL` | `redis://redis:6379/0` | Conexión Redis |
| `MS_DEFAULT_CONNECTION_ID` | `ms-default` | connection_id por defecto para Microsoft |

---

## Tokens: tiempos de vida

### Xero (authorization_code flow)
- **access_token**: expira en **30 minutos** (Xero estándar). El `expires_at` exacto se almacena en Redis.
- **refresh_token**: rotatorio. Xero invalida el anterior en cuanto se hace un refresh. Sin fecha de expiración oficial, pero prácticamente caduca si no se usa en ~60 días.
- **Refresh proactivo**: se activa cuando `now + REFRESH_BUFFER_SECONDS (300s) >= expires_at`, es decir, 5 minutos antes de caducar.
- **Lock de refresh**: TTL = 30 s. Previene que dos workers refresquen a la vez con el mismo refresh_token.

### Microsoft Graph (device-code / delegated flow)
- **access_token**: expira en **1 hora** (Azure estándar). El `expires_at` exacto se almacena en Redis.
- **refresh_token**: present — se usa para renovar sin intervención del operador.
- **Mismo REFRESH_BUFFER_SECONDS** y mismo mecanismo de lock que Xero.
- La autorización inicial requiere que un operador complete el device-code flow una sola vez.

---

## Estructuras Redis

### 1. `token:{connection_id}` — Hash

Almacena el TokenSet de una conexión OAuth (Xero o Microsoft).

| Campo Redis | Tipo | Ejemplo | Notas |
|---|---|---|---|
| `access_token` | string | `eyJ0eXAiOiJKV1...` | Bearer token para llamadas a la API |
| `refresh_token` | string | `1/fFAGRNJru...` o `""` | `""` = ausente (MS client_credentials no tiene) |
| `expires_at` | ISO-8601 UTC | `2026-04-21T14:35:00+00:00` | Momento exacto de expiración |
| `token_type` | string | `Bearer` | Siempre `Bearer` |
| `scope` | string | `offline_access accounting.invoices` o `""` | `""` = ausente |
| `xero_tenant_id` | string | `a1b2c3d4-...` o `""` | Solo Xero; `""` para Microsoft |

**TTL**: ninguno — persiste hasta revocación manual o re-autorización.

**Ejemplos de `connection_id`**:
- `ms-default` (Microsoft, valor de `MS_DEFAULT_CONNECTION_ID`)
- `xero-default`, `xero-prod`, cualquier string elegido al iniciar el OAuth

**Consultar en Redis**:
```bash
docker compose exec redis redis-cli HGETALL token:xero-default
docker compose exec redis redis-cli KEYS "token:*"
```

---

### 2. `oauth:state:{state}` — String

Mapeo temporal del parámetro `state` CSRF al `connection_id`, durante el callback OAuth de Xero.

| Campo | Valor |
|---|---|
| Clave | `oauth:state:<uuid-aleatorio>` |
| Valor | `connection_id` (ej: `xero-default`) |
| TTL | `OAUTH_STATE_TTL_SECONDS` (600 s = 10 min) |

Se consume de forma atómica (WATCH/MULTI/EXEC) al recibir el callback. Si expira o ya fue consumido, el callback falla con error.

---

### 3. `idempotency:{operacion}:{key}` — String (JSON)

Caché de resultados de operaciones write para evitar duplicados en retries de OpenClaw.

| Campo | Valor |
|---|---|
| Clave | `idempotency:<operacion>:<idempotency_key>` |
| Valor | JSON con el resultado cacheado |
| TTL | `IDEMPOTENCY_TTL_SECONDS` (86400 s = 24 h) |

**Operaciones que usan idempotencia**:

| Prefijo operación | Qué cachea |
|---|---|
| `idempotency:create_xero_invoice:{key}` | `{"invoice_id": "...", "status": "DRAFT"}` |
| `idempotency:submit_xero_invoice:{key}` | `{"invoice_id": "...", "status": "AUTHORISED"}` |
| `idempotency:void_xero_invoice:{key}` | `{"invoice_id": "...", "status": "VOIDED"}` |
| `idempotency:send_teams_message:{key}` | resultado del envío |

---

### 4. `lock:refresh:{connection_id}` — String

Lock distribuido para serializar el refresh de tokens. Evita que dos workers refresquen en paralelo con el mismo `refresh_token` (crítico para Xero por la rotación).

| Campo | Valor |
|---|---|
| Clave | `lock:refresh:<connection_id>` |
| Valor | UUID aleatorio del worker que adquirió el lock |
| TTL | 30 s (auto-liberación si el worker muere) |
| Reintentos | 5 intentos con 100 ms de espera entre cada uno |

El lock se libera con Lua script (SET NX + WATCH/DELETE) para garantizar que solo el propietario lo libere.

---

### 5. `approval:{approvalId}` — Hash

Estado completo de una solicitud de aprobación de factura.

| Campo Redis | Tipo | Ejemplo | Notas |
|---|---|---|---|
| `approval_id` | string | `appr-20260421-001` | Generado por OpenClaw |
| `invoice_case_id` | string | `case-xyz-123` | Session key del agente OpenClaw |
| `pdf_path` | string | `/storage/invoices/case-xyz-123.pdf` | Ruta al PDF, reenviada al webhook |
| `invoice_number` | string | `BILL-0042` | Número de factura Xero (display) |
| `supplier_name` | string | `Acme Corp` | Nombre del proveedor (display) |
| `approve_url` | string | `https://…/approvals/…/approve` | URL generada por OpenClaw |
| `reject_url` | string | `https://…/approvals/…/reject` | URL generada por OpenClaw |
| `status` | string | `pending` / `resolved` | Lifecycle state |
| `decision` | string | `approved` / `needs_changes` / `rejected` o `""` | `""` mientras pending |
| `note` | string | `"Please fix line 2"` o `""` | Obligatoria si `decision=needs_changes` |
| `created_at` | ISO-8601 UTC | `2026-04-21T10:00:00+00:00` | |
| `decided_at` | ISO-8601 UTC o `""` | `2026-04-21T10:05:00+00:00` | `""` mientras pending |
| `decision_source` | string | `web_form` o `""` | Quién tomó la decisión |
| `webhook_sent_at` | ISO-8601 UTC o `""` | `2026-04-21T10:05:01+00:00` | `""` mientras pending |
| `webhook_result` | string | `ok` o `HTTP 503` o `""` | Resultado de notificar a OpenClaw |

**TTL**: ninguno — los registros se retienen indefinidamente.

**Compatibilidad retroactiva**: registros anteriores al refactor (que guardaban `status=approved` o `status=rejected`) se migran automáticamente en lectura: `status` se convierte a `resolved` y el valor antiguo se mueve a `decision`.

---

## Flujo de datos: creación de factura Xero

```
OpenClaw → POST /v1/xero/invoices
         → idempotency check (Redis: idempotency:create_xero_invoice:{key})
         → XeroTokenManager.get_valid_token(connection_id)
               → Redis HGETALL token:{connection_id}
               → si expira en < 300s: lock:refresh:{connection_id} → OAuth refresh → Redis HSET
         → POST https://api.xero.com/api.xro/2.0/Invoices  (Type: ACCPAY)
         → valida que Xero devuelva Type=ACCPAY
         → Redis SET idempotency:create_xero_invoice:{key}  (TTL 86400s)
```

## Flujo de datos: aprobación de factura

```
OpenClaw → POST /internal/approvals/register
         → Redis HSET approval:{approvalId}  (status=pending)

Usuario  → GET  /approvals/{id}/reject  →  página con dropdown
         → POST /approvals/{id}/decision (decision=needs_changes|rejected, note=...)
         → Redis HSET approval:{approvalId}  (status=resolved, decision=..., note=...)
         → POST {OPENCLAW_WEBHOOK_URL}/hooks/agent  (action=..., note=...)
```
