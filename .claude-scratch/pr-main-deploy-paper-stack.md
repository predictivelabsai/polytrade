# Point main's deploy compose at the paper platform

## What

- Raslen's merge of #39 resolved the `docker-compose.yaml` conflict by keeping
  the legacy chat compose, so `main` ships the paper-platform code but still
  deploys the chat stack (verified live: `api.polytrade.chat/health` reports
  `v3.0.0`, `grok-4-fast-reasoning` — the legacy API).
- Root `docker-compose.yaml` now builds the paper platform
  (`redis`, `gateway`, `backtest-api`, `backtest-worker`, `agent`, `web`) —
  same compose that was in prod since August.
- The legacy chat compose (hermes, api, web, agui) moves to
  `docker-compose.chat.yaml` unchanged, so the chat stack keeps deploying from
  this repo (e.g. to `chat.polytrade.chat`). Nothing from Raslen's Hermes /
  activity-logging work is touched.
- `.env.example` becomes a union: paper-stack vars as the base, chat-stack-only
  vars appended under `# --- Legacy chat stack ---`. `CORS_ORIGINS` is shared —
  it must now be the paper web origin (`https://polytrade.chat`).

## Verification

- `docker compose -f docker-compose.yaml config` OK (with required vars set);
  `docker compose -f docker-compose.chat.yaml config` OK.
- All four build paths in the paper compose exist on `main`
  (`apps/gateway/Dockerfile`, `apps/web/Dockerfile`,
  `services/agent/Dockerfile`, `services/backtest/Dockerfile`).
- `pnpm typecheck`, `pnpm test`, `pnpm test:agent`, `pnpm test:backtest` all
  green on the release branch this compose was taken from.

## Notes

- After merge, Coolify keeps deploying from `main` — it now builds the paper
  platform and reclaims `polytrade.chat` / `api.polytrade.chat`.
- Chat stack needs its own domain (e.g. `chat.polytrade.chat`, new IONOS A
  record) and its own Coolify compose-file setting if it stays deployed.
- DB schema re-apply required after first deploy (strategy alerts, track
  records, scorecard migrations), per the earlier feature PRs.