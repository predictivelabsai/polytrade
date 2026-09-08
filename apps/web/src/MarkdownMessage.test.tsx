/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownMessage } from "./MarkdownMessage";

describe("MarkdownMessage", () => {
  it("renders agent emphasis, nested lists, and identifiers as semantic Markdown", () => {
    const { container } = render(
      <MarkdownMessage source={[
        "**2024 US Presidential Election Winner**:",
        "",
        "1. **Will Donald Trump win?** — resolved Yes",
        "   - condition ID: `0xdd222472e552920b8438158ea7238bfadfa4f736aa4cee91a6b86c39ead110917`",
      ].join("\n")} />,
    );

    expect(screen.getByText("2024 US Presidential Election Winner").tagName).toBe("STRONG");
    const ordered = container.querySelector("ol");
    expect(ordered).toBeInTheDocument();
    expect(screen.getByText("Will Donald Trump win?").tagName).toBe("STRONG");
    expect(container.querySelector("ol ul")).toBeInTheDocument();
    expect(screen.getByText(/^0xdd222/).tagName).toBe("CODE");
    expect(container).not.toHaveTextContent("**");
  });

  it("supports GFM tables and safe external links without rendering raw HTML", () => {
    const { container } = render(
      <MarkdownMessage source={[
        "| Market | Result |",
        "| --- | --- |",
        "| Election | Yes |",
        "",
        "[Polymarket](https://polymarket.com)",
        "",
        "<script>alert('unsafe')</script>",
      ].join("\n")} />,
    );

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Polymarket" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Polymarket" })).toHaveAttribute("rel", "noreferrer noopener");
    expect(container.querySelector("script")).not.toBeInTheDocument();
  });
});
