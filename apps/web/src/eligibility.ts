import { z } from "zod";

export const POLYMARKET_BROWSER_GEOBLOCK_URL = "https://polymarket.com/api/geoblock";

export interface Eligibility {
  blocked: boolean;
  verified: boolean;
  country: string;
  region: string;
  checkedAt: string;
}

const geoblockResponseSchema = z.object({
  blocked: z.boolean(),
  country: z.string().trim().regex(/^[A-Za-z]{2}$/),
  region: z.string().trim().max(16).nullish().transform((value) => value ?? ""),
});

function unverifiedEligibility(): Eligibility {
  return {
    blocked: true,
    verified: false,
    country: "",
    region: "",
    checkedAt: new Date().toISOString(),
  };
}

export async function checkBrowserEligibility(
  request: typeof fetch = fetch,
): Promise<Eligibility> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await request(POLYMARKET_BROWSER_GEOBLOCK_URL, {
      credentials: "omit",
      signal: controller.signal,
    });
    if (!response.ok) return unverifiedEligibility();

    const parsed = geoblockResponseSchema.safeParse(await response.json());
    if (!parsed.success) return unverifiedEligibility();

    return {
      blocked: parsed.data.blocked,
      verified: true,
      country: parsed.data.country.toUpperCase(),
      region: parsed.data.region.toUpperCase(),
      checkedAt: new Date().toISOString(),
    };
  } catch {
    return unverifiedEligibility();
  } finally {
    globalThis.clearTimeout(timeout);
  }
}
