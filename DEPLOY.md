# PolyTrade Deployment — Coolify + Hostinger VPS

## Domain
All services use a single domain: `polytrade.chat`

| Service | Domain | Port |
|---------|--------|------|
| AG-UI Chat | `polytrade.chat` / `www.polytrade.chat` | 4003 |
| Web Shell | `app.polytrade.chat` | 4002 |
| REST API | `api.polytrade.chat` | 4000 |

### Step 1: Register Domain
Register `polytrade.chat` at your domain registrar (IONOS, etc.)

### Step 2: Add DNS Records in IONOS
Add A records pointing to your Hostinger VPS IP:

| Type | Host | Value | TTL |
|------|------|-------|-----|
| A | @ | `<VPS_IP>` | 3600 |
| A | www | `<VPS_IP>` | 3600 |
| A | api | `<VPS_IP>` | 3600 |
| A | app | `<VPS_IP>` | 3600 |

### Step 3: Deploy on Coolify

1. **Add resource** in Coolify:
   - New Resource → Public Repository
   - URL: `https://github.com/predictivelabsai/polytrade`
   - Branch: `main`
   - Build Pack: **Docker Compose**

2. **Set environment variables** in Coolify:
   All env vars from `.env.example`:
   ```
   MODEL=grok-4-fast-reasoning
   MODEL_PROVIDER=xai
   XAI_API_KEY=...
   OPENAI_API_KEY=...
   FINANCIAL_DATA_PROVIDER=financial_datasets
   FINANCIAL_DATASETS_API_KEY=...
   POLYMARKET_WALLET_PRIVATE_KEY=...
   TOMORROWIO_API_KEY=...
   VISUAL_CROSSING_API_KEY=...
   DATABASE_URL=...
   POLYCODE_DB_URL=...
   JWT_SECRET=... # long random value, shared by API and web services
   ASSETHERO_CLIENT_ID=assethero
   ASSETHERO_CLIENT_SECRET=... # generate with: openssl rand -hex 32
   SERVICE_TOKEN_TTL_SECONDS=900
   SERVICE_AUTH_RATE_LIMIT_PER_MINUTE=10
   SERVICE_CHAT_RATE_LIMIT_PER_MINUTE=120
   CORS_ORIGINS=https://polytrade.chat,https://www.polytrade.chat,https://app.polytrade.chat
   ```

   Keep `ASSETHERO_CLIENT_SECRET` on the AssetHero backend only. It exchanges
   that credential at `/v1/auth/service-token`; neither the credential nor the
   resulting shared service token belongs in browser code.

3. **Configure domains** in Coolify for each service:
   - `api` service → `https://api.polytrade.chat` (port 4000)
   - `web` service → `https://app.polytrade.chat` (port 4002)
   - `agui` service → `https://polytrade.chat,https://www.polytrade.chat` (port 4003)

4. **Enable SSL** — Coolify auto-provisions Let's Encrypt certificates

5. **Deploy** — Click deploy. Coolify builds all 3 Docker images and starts them.

### Step 4: Verify

```bash
# Health checks
curl https://api.polytrade.chat/health
curl https://app.polytrade.chat/health
curl https://polytrade.chat/health

# Obtain an API token, create a thread, then use /v1/threads/{id}/messages.
# Full examples: docs/chat_api.md
```

Apply `db/migrations/001_shared_chat_api.sql` before the first deployment that
uses the shared chat API.

## Local Testing

```bash
# Individual services
python api/main.py       # port 4000
uv run --project apps/web uvicorn polytrade_web.app:app --port 8080
uv run --project apps/gateway uvicorn polytrade_gateway.main:app --port 4000

# All via Docker Compose
docker-compose up --build
```
