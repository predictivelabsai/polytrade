import { Check, ChevronDown, X } from "lucide-react";
import { useState } from "react";

const STORAGE_KEY = "polytrade.first-value-guide-dismissed";

export type FirstValueStep = "template" | "market" | "review" | "running";

const COPY: Record<FirstValueStep, { title: string; body: string }> = {
  template: {
    title: "Choose a ready-made plan",
    body: "Start with an illustrative template. Its band, size, and cadence stay editable before it runs.",
  },
  market: {
    title: "Choose a live market",
    body: "The template found matching markets. Pick one to translate its price band onto the live order book.",
  },
  review: {
    title: "Review, then start in paper",
    body: "Check the order size and price band in the runner. Starting it uses virtual USDC and can be stopped at any time.",
  },
  running: {
    title: "Your first paper strategy is running",
    body: "The runner is now watching the market in the background. Its activity and simulated fills stay in this ledger.",
  },
};

export function FirstValueGuide(props: { step: FirstValueStep }) {
  const [dismissed, setDismissed] = useState(() => window.localStorage.getItem(STORAGE_KEY) === "true");
  const dismiss = () => {
    window.localStorage.setItem(STORAGE_KEY, "true");
    setDismissed(true);
  };
  const scrollToTemplates = () => document.getElementById("strategy-templates")?.scrollIntoView({ behavior: "smooth", block: "start" });
  const copy = COPY[props.step];

  if (dismissed) return null;

  return (
    <section className="first-value-guide" aria-label="First paper strategy guide">
      <div className="first-value-guide-progress" aria-label={`Step ${stepNumber(props.step)} of 3`}>
        {["template", "market", "review"].map((step, index) => (
          <span className={index < stepNumber(props.step) ? "is-complete" : index === stepNumber(props.step) ? "is-current" : ""} key={step}>
            {index < stepNumber(props.step) ? <Check aria-hidden="true" /> : index + 1}
          </span>
        ))}
      </div>
      <div className="first-value-guide-copy">
        <span className="eyebrow">First paper strategy · about two minutes</span>
        <strong>{copy.title}</strong>
        <p>{copy.body}</p>
      </div>
      <div className="first-value-guide-actions">
        {props.step === "template" ? <button className="button button-primary" type="button" onClick={scrollToTemplates}>Choose a template <ChevronDown aria-hidden="true" /></button> : null}
        {props.step === "running" ? <button className="button button-quiet" type="button" onClick={dismiss}>Done</button> : null}
        <button className="icon-button" type="button" onClick={dismiss} aria-label="Dismiss first paper strategy guide"><X /></button>
      </div>
    </section>
  );
}

function stepNumber(step: FirstValueStep): number {
  return ({ template: 0, market: 1, review: 2, running: 3 })[step];
}
