import type { StrategyTemplate } from "@polytrade/contracts";
import { ArrowRight, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";

/**
 * Card row of pre-built strategy templates. In the workspace the Deploy
 * button arms the strategy runner; on the public landing page it links to
 * /paper?template=… so sign-in happens at the destination.
 */
export function StrategyTemplateGrid(props: {
  templates: StrategyTemplate[];
  onDeploy?: (template: StrategyTemplate) => void;
  deployPath?: (template: StrategyTemplate) => string;
  runningBlocked?: boolean;
  variant?: "workspace" | "landing";
}) {
  const landing = props.variant === "landing";
  return (
    <section className={`template-grid-section ${landing ? "template-grid-landing" : ""}`} id="strategy-templates" aria-label="Strategy templates">
      <header className="template-grid-header">
        <div>
          <span className="eyebrow">One-click strategies</span>
          <h2>Start from a proven template</h2>
        </div>
        {props.runningBlocked ? <p className="template-grid-note">Stop the running strategy to deploy a template.</p> : null}
      </header>
      <div className="template-grid">
        {props.templates.map((template) => (
          <article key={template.id} className="template-card">
            <div className="template-card-heading">
              <h3>{template.name}</h3>
              <span className="template-card-kind">Illustrative</span>
            </div>
            <p className="template-card-tagline">{template.tagline}</p>
            {landing ? <p className="template-card-description">{template.description}</p> : null}
            <dl className="template-card-stats">
              <div><dt>Return</dt><dd>+{template.stats.returnPct}%</dd></div>
              <div><dt>Win rate</dt><dd>{template.stats.winRatePct}%</dd></div>
              <div><dt>Trades</dt><dd>{template.stats.tradeCount}</dd></div>
              <div><dt>Max drawdown</dt><dd>−{template.stats.maxDrawdownPct}%</dd></div>
            </dl>
            <p className="template-card-basis"><span>Evidence</span>{template.stats.basis}</p>
            {landing ? (
              <div className="template-card-actions">
                <Link className="button button-primary" to={props.deployPath?.(template) ?? `/paper?template=${template.id}`}>
                  Deploy to paper <ArrowRight aria-hidden="true" />
                </Link>
                <Link className="button button-quiet" to={`/backtests/new?template=${template.id}`}>See the backtest setup</Link>
              </div>
            ) : (
              <div className="template-card-actions">
                <button
                  className="button button-primary"
                  type="button"
                  disabled={props.runningBlocked}
                  onClick={() => props.onDeploy?.(template)}
                >
                  <Sparkles aria-hidden="true" /> Deploy to paper
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
