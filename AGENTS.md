# AGENTS.md — session state for AI coding agents (Codex, Claude, …)

Local, uncommitted notes for continuing work on this machine. Design direction
lives in the committed `.impeccable.md` / `.github/copilot-instructions.md`
(see PR #31). Last updated: 2026-09-03.

## Repository conventions (learned this session — keep following them)

- PRs target the integration branch **`feature/hermes-runtime`** (updated
  2026-09-09; #40–#43 merged there). Earlier note said
  `feature/paper-trading-v1` — that era is done; `main` is stale legacy.
- House PR body style: `## What` / `## Verification` / `## Notes` bullets,
  concise, file paths in backticks (example: `.claude-scratch/pr3-body.md`).
- Branch naming: `feature/*`, `fix/*`, `design/*`. Commit style: short
  imperative single line ("Extend wallet session lifetime and add one-click
  reconnect"). No AI attribution in commits or PR bodies.
- Verification: `pnpm typecheck`, `pnpm test` (contracts+web+gateway),
  `pnpm test:agent`, `pnpm test:backtest`. CI also runs `pnpm audit --prod
  --audit-level high` and docker-compose builds.
- Web tests: Vitest + Testing Library, mocks via `vi.mock` factories in
  `RoutedApp.test.tsx` (mock registry `mocks.*`). Gotcha: never use
  `findBy*`/waitFor under `vi.useFakeTimers` (its interval freezes → 5s
  timeout leaks fake timers into later tests); flush with `await act(async () => {})`
  and use `getBy*` instead.

## Open PR queue — merge in this order

0. **PR #43** "Balance the /paper page columns when a background strategy
   runs" — `fix/paper-bottom-column-balance` → `feature/hermes-runtime`.
   Commit `7f52838`: ShareCard moved from the ticket rail into
   `paper-data-column` after Paper fills (fixes the bottom dead zone when a
   strategy runs), `.share-card` spacing touch-up, `paper-strategy` scenario
   added to `scripts/walkthrough.mjs`. Screenshots: `shots/paper-bottom-fix/`.
1. **PR #30** "Keep wallet sessions connected: longer idle window and
   one-click reconnect" — `fix/wallet-session-expiry` → `feature/paper-trading-v1`.
   CI fully green. Commits: `ac5b340` (idle TTL 30min→4h default/8h max,
   absolute 8h→24h; web: expiry notice once, one-click reconnect with
   localStorage-prefilled wallet type/funder, poll syncs fresh expiry via
   non-renewing `GET /v1/wallet-sessions/current`) + `bc7bb21` (`fast-uri`
   pnpm overrides `@3→3.1.6`, `@4→4.1.3` to clear the CI `pnpm audit` gate —
   audit fails on newly published advisories; add overrides in `package.json`
   next to the existing `ws` override when it drifts again).
2. **PR #31** "Repair workspace layout drift and persist design context" —
   `design/layout-audit-fixes` → `feature/paper-trading-v1`, **stacked on #30**
   (branch contains #30's commits + design commit `763d8c0`). After #30
   merges, #31's diff auto-shrinks; merge immediately after. Content: settings
   two-column restructure, mobile overflow root fix (all responsive `1fr`
   tracks → `minmax(0, 1fr)`; nowrap tables forced 733px page width), paper
   ledger strip unification, template stat grid alignment, `td.num` right-
   aligned tabular numeric columns (positions/orders/fills/holdings/paper
   fills/execution ledger/comparison), removed repeated "Account ledger"
   eyebrow from `DataSection` (App.tsx export used by RoutedApp), config-strip
   lone-item span, `.impeccable.md` + `.github/copilot-instructions.md`,
   `scripts/design-audit-shots.mjs` + `scripts/design-audit-probe.mjs`.
3. **PR #32** "Add template stats sweep behind the template card numbers" —
   `feature/template-stats-sweep` → `feature/paper-trading-v1`, stacked on #30
   (audit-gate fix only; no functional dependency on #30/#31). Commit
   `ef8fe22`: `services/backtest/scripts/template_stats_sweep.py` +
   `tests/test_template_stats_sweep.py` (two-phase universe-manifest sweep,
   disk cache, publishable floor, drift guard vs `strategyTemplates`). While
   landing: fixed Decimal/str volume handling in `rank_and_cap`, JSON-safe
   `universe_document`, E501/S106 lint. Ruff clean, 45 passed / 4 skipped.
   Independent of #31 — can merge any time after #30.
4. **PR #33** "Stop the /paper page from overflowing horizontally" —
   `fix/paper-overflow` → `feature/paper-trading-v1`, based on post-#31
   integration. Commit `3c1def2`: /paper horizontal overflow root-caused to
   two bare single-column grids whose implicit auto tracks sized to content
   max-content — `.paper-ticket-column` (inflated to 515px by the share-card
   token row's nowrap URL when sharing is enabled) and `.paper-data-column`
   (inflated to ~960px by nowrap fills/holdings tables at 768–1199px).
   Fix: `grid-template-columns: minmax(0, 1fr)` on both in `styles.css`
   (same convention as PR #31). Verified with a Playwright sweep
   (10 viewports 320–1920 × running/ready strategy × loaded/search/quote
   states, share link enabled): all `scrollWidth == clientWidth`, zero
   offenders. CI green (agent/backtest/containers/typescript).

## Design decisions (source of truth: `.impeccable.md`, committed in #31)

- Public product for Polymarket traders; premium/exclusive feel; between
  Kalshi and Linear/Vercel; dark-only; palette/fonts unchanged (#06110d
  canvas, #34d399 accent, Manrope + IBM Plex Mono). Anti-refs: crypto casino,
  cluttered terminal, legacy banking, toy demo. A11y: best effort.
- Principles: Money looks serious · Numbers are sacred · Calm through
  whitespace · One system, no drift · Restraint over decoration.
- User said redesign "looks great" after the audit fixes.

## Uncommitted / local-only work (do not lose)

- Root `scripts/*-visual.mjs` (older per-feature Playwright harnesses),
  `shots/` (screenshots incl. `shots/audit/{before,after}`),
  `.claude-scratch/` (PR body drafts), `apps/web/public/wallet-test.html`.
- This file (`AGENTS.md`) is intentionally uncommitted. `CLAUDE.local.md`
  points here for Claude sessions.

## Environment state on this machine

- Vite dev server left **running detached on http://localhost:5173**
  (`cmd /c pnpm dev:web` hidden window). Kill with `Get-Process node |
  Stop-Process` if needed; restart with `pnpm dev:web`.
- `apps/web/.env.local`: `VITE_E2E_AUTH_BYPASS=1` (skips Clerk UI only) and
  `VITE_API_URL=https://api.polytrade.chat` → without a real gateway session,
  Paper/Backtests/Trades show "Failed to fetch" locally. **Expected.** The
  visual harness mocks `**/v1/**` (plus `https://polymarket.com/api/geoblock`)
  — see `scripts/design-audit-shots.mjs`; run `node scripts/design-audit-shots.mjs
  after` to regenerate screenshots into `shots/audit/after/`.
- Playwright scripts use `chromium.launch({ channel: "chrome" })` (system
  Chrome, no browser download).
- Mock-fixture gotchas for the harness: backtest `resolvedOutcome` must be
  uppercase `"YES"/"NO"`; paper fills need UUID `fillId`, `origin`,
  `conditionId`, `tokenId`, `grossNotional`, `feeRate`, non-null
  `realizedPnl`; paper-strategy eventIds/fillIds must be UUIDs.

## Suggested next steps

1. Merge #30, then #31, then #32 (any time after #30; watch each PR's CI).
2. Optional follow-ups deferred from the wallet fix: "expiring soon" countdown
   banner in the chat activity card (low value now that the poll renews while
   visible), audit gate drift monitoring.
3. When the sweep's published numbers get refreshed, run the two-phase flow
   from the script docstring and commit the new manifest + contracts constant.
