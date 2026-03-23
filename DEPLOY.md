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
   ```

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

# Test API
curl -X POST https://api.polytrade.chat/agent/run \
  -H "Content-Type: application/json" \
  -d '{"query":"What is AAPL stock price?"}'
```

## Local Testing

```bash
# Individual services
python api/main.py       # port 4000
python web_app.py        # port 4002
python agui_app.py       # port 4003

# All via Docker Compose
docker-compose up --build
```
