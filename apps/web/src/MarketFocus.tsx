import { BarChart3, MessageSquare, X } from "lucide-react";
import type { MarketSearchMarket } from "@polytrade/contracts";
import type { ReactNode } from "react";
import { Link, useInRouterContext } from "react-router-dom";

import { focusHref } from "./market-focus";

export function MarketFocus(props: { market: MarketSearchMarket; onClear?: () => void; showBacktestAction?: boolean }) {
  const state = props.market.active && !props.market.closed ? "Live market" : "Resolved market";
  const backtestEligible = !props.market.active && props.market.closed;
  return (
    <section className="market-focus" aria-label="Market in focus">
      <div>
        <span className="eyebrow">Market in focus</span>
        <strong>{props.market.question}</strong>
        <small>{state} · {props.market.outcomes.map((outcome, index) => `${outcome} ${formatPrice(props.market.outcomePrices[index])}`).join(" / ")}</small>
      </div>
      <div className="market-focus-actions">
        <HandoffLink market={props.market} to="/chat/new"><MessageSquare /> Ask agent</HandoffLink>
        {backtestEligible && props.showBacktestAction !== false ? (
          <HandoffLink market={props.market} to="/backtests/new"><BarChart3 /> Backtest</HandoffLink>
        ) : !backtestEligible ? <span className="market-focus-note">Backtests use resolved markets</span> : null}
        {props.onClear ? <button className="icon-button" type="button" onClick={props.onClear} aria-label="Clear market focus"><X /></button> : null}
      </div>
    </section>
  );
}

function HandoffLink(props: { children: ReactNode; market: MarketSearchMarket; to: string }) {
  const href = focusHref(props.to, props.market);
  const inRouter = useInRouterContext();
  return inRouter
    ? <Link className="button button-quiet" to={href}>{props.children}</Link>
    : <a className="button button-quiet" href={href}>{props.children}</a>;
}

function formatPrice(value: string | undefined): string {
  const price = Number(value);
  return Number.isFinite(price) ? price.toFixed(2) : "—";
}
