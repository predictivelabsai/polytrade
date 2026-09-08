# Copilot instructions — PolyTrade

## Design Context

### Users
Self-serve public product for Polymarket traders and researchers. Strangers
arrive from the public surfaces (`/templates`, `/u/:token`, `/accuracy`) and
must be able to onboard themselves; they then live in the authenticated
workspace (Chat, Paper, Backtests, Trades, Settings). Money moments — Trades,
wallet connect, order signing — are the highest-trust surfaces and must never
feel casual. The audience mixes crypto-native traders with newcomers:
onboarding must not assume expertise, and the interface must never condescend
to experts.

### Brand Personality
**Composed, precise, exclusive.** The feel of quiet money — a well-run
trading desk, not a hype channel. Understated confidence: numbers speak,
chrome stays silent. Premium is expressed through discipline (alignment,
whitespace, restraint), never through gradients, glow, or gamification.

### Aesthetic Direction
Sits between **Kalshi** (modern retail trading: clean cards, legible data,
honest UI) and **Linear/Vercel** (dark, minimal, crisp typography, generous
whitespace, one restrained accent). Dark-only theme: near-black green canvas
`#06110d`, pane `#0b1b14`, emerald accent `#34d399`, Manrope Variable for UI
text, IBM Plex Mono for every number, ticker, and code surface. Anti-
references: crypto casino, cluttered terminal, legacy banking, toy/demo
pastel. None of these may leak in — no neon gradients, no wall-of-numbers
density without structure, no corporate stiffness, nothing that reads as a
fake-money demo.

### Design Principles
1. **Money looks serious.** Surfaces that touch real funds (Trades, wallet
   verification, order signing) get flawless alignment, explicit labels, and
   zero clutter near the action.
2. **Numbers are sacred.** Monospace everywhere, right-aligned in tables,
   consistent decimal treatment, P&L colors used consistently and never as
   the only signal.
3. **Calm through whitespace.** Breathing room is the luxury signal. Density
   is earned by structure (tables, ledgers) and never allowed to become
   noise.
4. **One system, no drift.** Page headers, section spacing, card padding,
   field styles, and pills follow one scale across every page. New features
   conform to the system; they never improvise a second one.
5. **Restraint over decoration.** The emerald accent is a tool for state and
   hierarchy, not a theme. Glow, gradients, and motion appear only where
   they carry meaning.
