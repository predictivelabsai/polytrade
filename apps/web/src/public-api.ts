// Signed-out fetches for public gateway reads. Deliberately mirrors nothing
// from GatewayClient: no Authorization header, no token provider — a share
// page must work for visitors who never mounted Clerk.
import { publicTrackRecordSchema, type PublicTrackRecord } from "@polytrade/contracts";

import { GatewayError } from "./api";

export async function fetchPublicTrackRecord(baseUrl: string, token: string): Promise<PublicTrackRecord> {
  const response = await fetch(new URL(`/v1/public/track-records/${encodeURIComponent(token)}`, baseUrl), {
    headers: { Accept: "application/json" },
    credentials: "omit",
  });
  const payload = (await response.json().catch(() => null)) as
    | { error?: { code?: string; message?: string } }
    | null;
  if (!response.ok) {
    throw new GatewayError(
      payload?.error?.message ?? `Gateway request failed (${response.status})`,
      payload?.error?.code ?? "GATEWAY_ERROR",
      response.status,
    );
  }
  return publicTrackRecordSchema.parse(payload);
}