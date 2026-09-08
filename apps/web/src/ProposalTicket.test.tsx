/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { ProposalTicket, type ProposalDraft } from "./App";

vi.mock("./env", () => ({
  env: {
    VITE_API_URL: "https://api.example.test",
    VITE_CLERK_PUBLISHABLE_KEY: "pk_test",
    VITE_CLERK_JWT_TEMPLATE: "polytrade",
  },
}));

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

const draft = {
  expiresAt: "2099-08-04T00:02:00.000Z",
  proposal: {
    action: "create",
    execution: "FAK",
    tokenId: "123",
    marketId: "condition-1",
    marketQuestion: "Will the risk guard be clear?",
    outcome: "Yes",
    side: "BUY",
    rationale: "Test the exact cash exposure.",
    observedAt: "2026-08-04T00:00:00.000Z",
    amount: "125",
    limitPrice: "0.55",
    postOnly: false,
  },
} satisfies ProposalDraft;

function TicketHarness() {
  const [reviewed, setReviewed] = useState(false);
  const [currentDraft, setCurrentDraft] = useState<ProposalDraft>(draft);
  return (
    <ProposalTicket
      draft={currentDraft}
      onChange={(proposal) => setCurrentDraft((current) => ({ ...current, proposal }))}
      onClear={vi.fn()}
      onExecute={vi.fn()}
      reviewed={reviewed}
      onReviewed={setReviewed}
      sessionReady
      tradeAllowed
      submitting={false}
      pendingSignedOrder={false}
    />
  );
}

it("requires both a cash guard and explicit worst-case confirmation before a real order can sign", async () => {
  const user = userEvent.setup();
  render(<TicketHarness />);

  expect(screen.getByRole("region", { name: "Order risk checks" })).toHaveTextContent("125 USDC");
  expect(screen.getByText(/Market observation is over two minutes old/i)).toBeInTheDocument();
  const sign = screen.getByRole("button", { name: /Sign and place real order/i });
  expect(sign).toBeDisabled();

  await user.click(screen.getByRole("checkbox", { name: /I reviewed the exact market/i }));
  await user.click(screen.getByRole("checkbox", { name: /I understand the worst case/i }));
  expect(sign).toBeDisabled();

  const guard = screen.getByLabelText("Browser cash guard (USDC)");
  await user.clear(guard);
  await user.type(guard, "150");
  expect(sign).toBeEnabled();
  expect(window.localStorage.getItem("polytrade.cash-exposure-limit")).toBe("150");

  const amount = screen.getByLabelText("Amount (USDC)");
  await user.clear(amount);
  await user.type(amount, "120");
  expect(sign).toBeDisabled();
});
