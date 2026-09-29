import { describe, expect, it } from "vitest";
import {
  createExperimentRequestSchema,
  walkForwardConfigSchema,
} from "./index.js";

describe("experiment contracts", () => {
  it("preserves independent markets and exact Cartesian grid inputs", () => {
    const result = createExperimentRequestSchema.parse({
      marketIds: ["a", "b"],
      strategies: [
        {
          baseConfig: { strategy: "mean_reversion_v1" },
          parameters: {
            reversionThreshold: ["0.03", "0.05", "0.07"],
            takeProfit: ["0.01", "0.015"],
            maxHoldMinutes: [1440, 2880, 4320],
          },
        },
      ],
    });
    expect(result.initialCapital).toBe("10000");
    expect(result.walkForward.folds).toBe(5);
    expect(result.strategies[0]!.parameters.maxHoldMinutes).toEqual([
      1440, 2880, 4320,
    ]);
  });
  it("rejects unknown or cross-strategy fields and unpaired window overrides", () => {
    expect(
      walkForwardConfigSchema.safeParse({ trainMinutes: 12 }).success,
    ).toBe(false);
    for (const parameters of [
      { reversionThreshold: ["0.1"] },
      { initialCapital: ["10"] },
      { stopLoss: ["2"] },
    ]) {
      expect(
        createExperimentRequestSchema.safeParse({
          marketIds: ["a"],
          strategies: [{ baseConfig: { strategy: "momentum_v1" }, parameters }],
        }).success,
      ).toBe(false);
    }
  });
});
