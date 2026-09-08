// Public, signed-out strategy-template landing page at /templates. Mounted
// outside the Clerk wrapper in main.tsx like /u/:token: everything here comes
// from the static contracts constant, so there are no network requests and no
// reason to authenticate a visitor just to browse.
import { ArrowRight, ListChecks, MousePointerClick, Zap } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";

import { strategyTemplates } from "@polytrade/contracts";

import { StrategyTemplateGrid } from "./TemplateGrid";

const STEPS = [
  {
    icon: MousePointerClick,
    title: "Pick a template",
    body: "Five pre-tuned price-band strategies, each with an illustrative backtest on resolved Polymarket markets.",
  },
  {
    icon: ListChecks,
    title: "Review the band",
    body: "We translate the template onto the market you pick — buy price, sell price, order size, and cadence are all editable before anything runs.",
  },
  {
    icon: Zap,
    title: "Deploy to paper",
    body: "The strategy runs on the gateway in the background with virtual USDC. Stop it any time from the paper dashboard.",
  },
];

export default function TemplatesPage() {
  // This page is meant to be indexed — unlike the private /u/:token share
  // pages, no robots noindex here.
  useEffect(() => {
    document.title = "Strategy templates · PolyTrade";
  }, []);

  return (
    <main className="detail-page templates-page">
      <header className="template-landing-hero">
        <span className="eyebrow">PolyTrade paper trading</span>
        <h1>Start paper trading in two minutes</h1>
        <p>
          Pick a pre-built strategy, point it at any Polymarket market, and deploy to a
          virtual-USDC paper account. No wallet, no real funds — every fill is simulated.
        </p>
        <Link className="button button-primary template-landing-cta" to="/paper">
          Open the paper dashboard <ArrowRight aria-hidden="true" />
        </Link>
      </header>

      <StrategyTemplateGrid templates={strategyTemplates} variant="landing" />

      <section className="template-landing-steps" aria-label="How it works">
        {STEPS.map((step) => (
          <article key={step.title}>
            <step.icon aria-hidden="true" />
            <h2>{step.title}</h2>
            <p>{step.body}</p>
          </article>
        ))}
      </section>

      <footer className="template-landing-footer">
        <p>
          Stats are illustrative backtests on resolved markets with virtual USDC — not a promise of
          future results, and not investment advice. Review every band before deploying.
        </p>
        <Link to="/paper">Create your free paper account →</Link>
      </footer>
    </main>
  );
}