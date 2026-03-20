# PolyTrade Deployment — Coolify + Hetzner

## Domains
- `polytrade.dev` — Web Shell (web_app.py)
- `polytrade.chat` — AG-UI Chat (agui_app.py)
- `api.polytrade.dev` — REST API (api/main.py)

### Step 1: Register Domains
- Register `polytrade.dev` and `polytrade.chat` at your domain registrar (IONOS, etc.)

### Step 2: Add DNS Records in IONOS
Add A records for both domains pointing to your Hetzner/Coolify server IP:

**polytrade.dev:**

| Type | Host | Value | TTL |
|------|------|-------|-----|
| A | @ | `<HETZNER_IP>` | 3600 |
| A | api | `<HETZNER_IP>` | 3600 |
| A | www | `<HETZNER_IP>` | 3600 |

**polytrade.chat:**

| Type | Host | Value | TTL |
|------|------|-------|-----|
| A | @ | `<HETZNER_IP>` | 3600 |

### Step 3: Deploy on Coolify

1. **Connect GitHub repo** in Coolify:
   - Go to Coolify dashboard → New Resource → Docker Compose
   - Connect to your GitHub repo: `predictivelabsai/polycode`
   - Branch: `dev` (or `main` after merge)

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
   ```

3. **Configure domains** in Coolify for each service:
   - `api` service → `api.polytrade.dev` (port 4000)
   - `web` service → `polytrade.dev` (port 4002)
   - `agui` service → `polytrade.chat` (port 4003)

4. **Enable SSL** — Coolify auto-provisions Let's Encrypt certificates

5. **Deploy** — Click deploy. Coolify builds all 3 Docker images and starts them.

### Step 4: Verify

```bash
# Health checks
curl https://api.polytrade.dev/health
curl https://polytrade.dev/health
curl https://polytrade.chat/health

# Test API
curl -X POST https://api.polytrade.dev/agent/run \
  -H "Content-Type: application/json" \
  -d '{"query":"What is AAPL stock price?"}'
```

## Services

| Service | Dockerfile | Port | Domain |
|---------|-----------|------|--------|
| REST API | Dockerfile.api | 4000 | api.polytrade.dev |
| Web Shell | Dockerfile.fasthtml | 4002 | polytrade.dev |
| AG-UI Chat | Dockerfile.agui | 4003 | polytrade.chat |

## Local Testing

```bash
# Individual services
python api/main.py       # port 4000
python web_app.py        # port 4002
python agui_app.py       # port 4003

# All via Docker Compose
docker-compose up --build
```
