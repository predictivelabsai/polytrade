/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import TemplatesPage from "./TemplatesPage";

afterEach(cleanup);

function renderPage() {
  return render(
    <MemoryRouter>
      <TemplatesPage />
    </MemoryRouter>,
  );
}

describe("TemplatesPage", () => {
  it("renders the hero and every template card with its illustrative stats", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "Start paper trading in two minutes" })).toBeInTheDocument();
    expect(screen.getByText("Base-rate divergence")).toBeInTheDocument();
    expect(screen.getByText("Longshot fade")).toBeInTheDocument();
    expect(screen.getByText("EV sniping")).toBeInTheDocument();
    expect(screen.getByText("Overreaction fade")).toBeInTheDocument();
    expect(screen.getByText("Resolution grinder")).toBeInTheDocument();
    expect(screen.getAllByText("Illustrative")).toHaveLength(5);
  });

  it("links each deploy button to the paper dashboard with the template armed", () => {
    renderPage();
    const links = screen.getAllByRole("link", { name: /Deploy to paper/ });
    expect(links).toHaveLength(5);
    expect(links[0]).toHaveAttribute("href", "/paper?template=base-rate-divergence");
    expect(links[1]).toHaveAttribute("href", "/paper?template=longshot-fade");
  });

  it("links the backtest setup buttons to pre-filled backtest launches", () => {
    renderPage();
    const links = screen.getAllByRole("link", { name: "See the backtest setup" });
    expect(links).toHaveLength(5);
    expect(links[0]).toHaveAttribute("href", "/backtests/new?template=base-rate-divergence");
  });

  it("shows the how-it-works steps and the illustrative-stats disclaimer", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "Pick a template" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Review the band" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Deploy to paper" })).toBeInTheDocument();
    expect(screen.getByText(/not investment advice/i)).toBeInTheDocument();
  });
});