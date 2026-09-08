# Shared Chat API

Both the existing AG-UI window and external clients use the same backend in
`chat/service.py`. The HTTP API is stateful: the server owns conversation
history, model configuration, tools, persistence, and user isolation.

DeepAgents is the default runtime. Prefix one message with `/hermes` to use the
isolated Hermes sidecar, or with `/deepagent` (`/deepagents` is an alias) to
explicitly use DeepAgents. Prefixes affect only one message; the next
unprefixed message returns to DeepAgents. Responses and runs persist the actual
`agent_framework` and `agent_name` in the `polycode` schema.

## Setup

1. Configure `JWT_SECRET`, the model/provider keys, and a PostgreSQL URL.
2. Apply the chat migration:

   ```bash
   psql "$POLYCODE_DB_URL" -f db/migrations/001_shared_chat_api.sql
   psql "$POLYCODE_DB_URL" -f db/migrations/003_agent_attribution.sql
   ```

   A fresh installation can instead run:

   ```bash
   python scripts/setup_polycode_db.py
   ```

3. Start the API on port 4000:

   ```bash
   python api/main.py
   ```

FastAPI documentation is available at `http://localhost:4000/docs`.
`GET /health` returns `503` when the configured database is unreachable or the
chat migration has not been applied.

If neither `POLYCODE_DB_URL` nor `DATABASE_URL` is set, chat uses an in-memory
development store. That mode is not durable and cannot share conversations
between separate processes. Docker deployments default
`CHAT_REQUIRE_DATABASE=true` and fail fast instead of silently using memory.

## Authentication

### PolyTrade users

Exchange an existing PolyTrade email/password login for a one-hour access
token:

```bash
curl -X POST http://localhost:4000/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"your-password"}'
```

Use the returned token as `Authorization: Bearer <token>`.

### AssetHero as one trusted application

AssetHero uses a dedicated client credential from its backend:

```text
AssetHero browser -> AssetHero backend -> PolyTrade API
```

Never put `ASSETHERO_CLIENT_SECRET`, `JWT_SECRET`, or the resulting access token
in browser code. A service token can access every thread owned by AssetHero.
The AssetHero backend must proxy chat requests and keep its mapping from each
AssetHero user/session to the corresponding PolyTrade `thread_id`.

Configure these values on the PolyTrade API service:

```env
ASSETHERO_CLIENT_ID=assethero
ASSETHERO_CLIENT_SECRET=<openssl-rand-hex-32-output>
SERVICE_TOKEN_TTL_SECONDS=900
SERVICE_AUTH_RATE_LIMIT_PER_MINUTE=10
SERVICE_CHAT_RATE_LIMIT_PER_MINUTE=120
```

Generate the secret once:

```bash
openssl rand -hex 32
```

Store the same client ID and client secret only on the AssetHero backend. Do
not share PolyTrade's `JWT_SECRET` with AssetHero. Exchange the client
credential using HTTP Basic authentication:

```bash
curl -X POST https://api.polytrade.chat/v1/auth/service-token \
  --user "$ASSETHERO_CLIENT_ID:$ASSETHERO_CLIENT_SECRET"
```

The response contains a chat-only bearer token with a default lifetime of 15
minutes. Cache it server-side until shortly before `expires_in`, then exchange
the client credential again. The exchange endpoint rejects browser requests
that carry an `Origin` header.

Because this flow is server-to-server, `https://assethero.chat` does not need
to be in `CORS_ORIGINS` unless its browser also makes separate direct requests
to the PolyTrade API. Direct browser use of the shared AssetHero token is not
safe.

### Attribute predictions to an AssetHero user

For prediction and backtest requests, AssetHero's backend delegates ownership
to the user who initiated the request. Send both headers with the service
token:

```bash
curl -X POST https://api.polytrade.chat/backtest \
  -H "Authorization: Bearer $POLYTRADE_SERVICE_TOKEN" \
  -H 'Content-Type: application/json' \
  -H "X-User-Id: $ASSETHERO_USER_ID" \
  -H 'X-User-Source: assethero' \
  -d '{"city":"Melbourne","lookback_days":7,"is_prediction":true}'
```

`X-User-Id` must be the AssetHero user's UUID. The API accepts these headers
only from the configured AssetHero service principal. Supplying just one
header, an invalid UUID, or the headers with an ordinary user token fails the
request. Requests without the headers retain native ownership behavior.

AssetHero can retrieve that user's attributed runs with the same service
token:

```bash
curl "https://api.polytrade.chat/runs?source=assethero&principal_id=$ASSETHERO_USER_ID" \
  -H "Authorization: Bearer $POLYTRADE_SERVICE_TOKEN"
```

The prediction run detail endpoint is `/runs/{run_id}`. The similarly named
`/v1/runs/{run_id}` endpoint belongs to the chat API and is not a prediction
run endpoint.

## Create a thread

```bash
curl -X POST http://localhost:4000/v1/threads \
  -H "Authorization: Bearer $POLYTRADE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"NVDA research"}'
```

The authenticated user owns the returned `thread_id`. A different user
receives a not-found response even if they know that ID.

## Send a non-streaming message

```bash
curl -X POST http://localhost:4000/v1/threads/THREAD_ID/messages \
  -H "Authorization: Bearer $POLYTRADE_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 7e447b3e-14c3-4ef4-ae47-c473c631eab2' \
  -d '{"content":"Analyse the current NVDA outlook","stream":false}'
```

Do not send chat history, model names, provider names, user IDs, tool lists, or
system prompts. The server controls those values and loads the thread history.

## Stream a response

```bash
curl -N -X POST http://localhost:4000/v1/threads/THREAD_ID/messages \
  -H "Authorization: Bearer $POLYTRADE_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -H 'Idempotency-Key: 9167eaf4-1aee-4219-8834-d81418ac7773' \
  -d '{"content":"Compare NVDA and AMD","stream":true}'
```

The response uses named Server-Sent Events:

- `run.started`
- `tool.started`
- `tool.completed`
- `message.delta`
- `message.completed`
- `run.completed`
- `run.failed`

`message.completed` contains the authoritative final message. Clients should
replace any locally accumulated deltas with that content.

Because this stream begins with a POST request, browser clients should use
`fetch()` and read `response.body`; the browser `EventSource` class only
supports GET.

Treat assistant markdown as untrusted content. Escape it or pass rendered HTML
through a maintained sanitizer before inserting it into the DOM.

## Recovery and idempotency

Every message request should send a stable `Idempotency-Key`. Repeating a
completed request returns the persisted result without calling the model
again. A key is bound to its original message content and cannot be reused for
a different message. If the header is omitted, the server generates one and
returns it in the `Idempotency-Key` response header, but a client-generated key
is safer for retrying a request whose response never arrived. Use:

```text
GET /v1/runs/{run_id}
GET /v1/threads/{thread_id}/messages
```

to recover state after a disconnect.

## Safety boundary

The generic chat API contains research and paper-analysis tools only.
`poly:buy`, `poly:sell`, wallet access, and the `place_real_order` tool are
blocked. The shared chat tool registry is constructed in read-only mode and
does not load wallet credentials. Any future real-money execution API must use
a separate `trade:execute` scope, structured parameters, explicit confirmation,
idempotency, and audit logging.

The old `/agent/run` and `/agent/stream` routes are authenticated compatibility
adapters. They call the shared backend and return deprecation headers; new
clients should use `/v1/threads`.
