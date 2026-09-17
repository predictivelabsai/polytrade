# Treat blank optional secrets as unset in the gateway config

## What

- Today's prod deploy of `main` (46cd0bf) failed: the gateway crashed at boot
  with `TELEGRAM_BOT_TOKEN: Too small: expected string to have >=10 characters`.
  The Coolify resource env had a blank `TELEGRAM_BOT_TOKEN=` line (copied from
  `.env.example`), and zod treats an empty string as present — so
  `z.string().min(10).optional()` rejected it and `parseConfig` killed the
  process before the container could ever serve `/health`.
- `optionalHttpsUrl` already handles this exact pattern for `ASSETHERO_API_*`.
  This PR adds the same preprocess as `optionalSecret` and uses it for
  `TELEGRAM_BOT_TOKEN`: blank/whitespace-only → unset. The alert sender already
  reports a clean "TELEGRAM_BOT_TOKEN is not configured on the gateway" error
  at send time when it is absent, so alerts keep working unchanged once a real
  token is configured.
- Also fixes the operational trap where Coolify injects resource env vars into
  every compose service, so a stray blank line can no longer take down the
  whole stack.

## Verification

- New config tests: blank token parses as `undefined`; a too-short placeholder
  (e.g. `123`) still throws.
- `pnpm --filter @polytrade/gateway test -- --run`: 149 passed (17 files).
- `pnpm typecheck`: green.

## Notes

- Deploying this does not require any env change; the failed prod deploy will
  succeed with `TELEGRAM_BOT_TOKEN` still blank. When Telegram alerts are
  turned on, paste the real bot token into the Coolify env (production and/or
  preview) and redeploy.