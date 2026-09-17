# Merge `feature/paper-trading-v1` into `main`

## What

- Restores `main` as the product/deploy branch: `main` becomes the paper-trading
  platform (monorepo: `apps/web`, `apps/gateway`, `packages/contracts`,
  `services/*`) — 78 commits of rebuild + late-Aug/Sept feature work.
- Merges `main` into the integration branch first, resolving 5 modify/delete
  conflicts (legacy files deleted in the rebuild, modified on `main`) by keeping
  the deletions: `api/main.py`, `api/security.py`, `db/repository.py`,
  `docs/chat_api.md`, `scripts/setup_polycode_db.py`.
- Also drops `main`'s 3 legacy-only additions from commit `83aa19e`
  ("Attribute AssetHero prediction runs", 2026-08-11), which target the retired
  chat stack and its `polycode` DB: `tests/test_run_attribution.py`,
  `tests/test_run_attribution_integration.py`,
  `db/migrations/002_assethero_run_attribution.sql`.

## Verification

- `git merge-base` confirms `main` diverged from the integration branch by
  exactly 1 commit (`83aa19e`), touching only files deleted in the rebuild.
- Full suite: `pnpm typecheck`, `pnpm test` (contracts+web+gateway),
  `pnpm test:agent`, `pnpm test:backtest` (results in Verification comments).

## Notes

- Follow-up: AssetHero prediction-run attribution has no equivalent in the new
  gateway architecture. If still required, port it as a fresh PR against
  `apps/gateway` + the platform DB schema — this merge does not carry it.
- After merge: point the Coolify deploy resource at `main`; prod should deploy
  from `main` only. Do not create long-lived product branches off `main`.
- Old merged branches (`fix/*`, `feature/*` from PRs #24–#38) will be pruned
  after this lands.