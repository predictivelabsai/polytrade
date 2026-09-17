## What

The public face of the agent accuracy scorecard: a signed-out, **indexable** `/accuracy` page that renders the gateway's public aggregate. Third and final PR of the scorecard; stacked on the gateway PR (#27), which it depends on for the `agentPredictionHitRate` contracts.

- **`public-api.ts`**: `fetchPublicAgentScorecard(baseUrl)` — credentials omitted, no token, zod-parsed, mirroring `fetchPublicTrackRecord`.
- **`AccuracyPage.tsx`**: mounted in `main.tsx` at `/accuracy` outside `StandaloneAuthentication` (like `/u/:token`), but deliberately **without** the noindex meta — this page is the public record, unlike secret share links. Layout: `page-title` hero, five summary tiles (hit rate, resolved, hits, pending, voided), "By category" table, "Recent graded predictions" table with Hit/Miss pills, and a footer disclaimer (past accuracy ≠ future results, not investment advice). Empty state when nothing has graded yet; loading and retryable-error states mirror `TrackRecord`.
- **`styles.css`**: one `.accuracy-page` block (summary grid, hit/miss pills using the existing `--accent-wash`/`--danger` tokens, responsive 2-column metrics under 700px).
- **Visual review**: `scripts/agent-accuracy-visual.mjs` mocks `GET /v1/public/agent-accuracy` and screenshots desktop, 390px mobile, and the error state to `shots/accuracy/` (no auth bypass needed — the page renders signed out). All assertions pass.

## Verification

- `pnpm --filter @polytrade/web test`: 53 passed (4 new: populated layout, empty state, indexable-title/no-noindex, transient-failure retry)
- `pnpm --filter @polytrade/web typecheck` + root `pnpm lint`: clean
- Visual script: 3 screenshots in `shots/accuracy/`, assertion pass

## Notes

- No identity surface: the page only reads the public aggregate, which the gateway already guarantees is principal-free (asserted in #27's tests).
- The page shows real data only once #27 is deployed (schema re-apply) and the agent tool PR starts recording calls; until then it renders the empty state.