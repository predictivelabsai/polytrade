import { z } from "zod";

const envSchema = z.object({
  VITE_API_URL: z.string().url(),
  VITE_CLERK_PUBLISHABLE_KEY: z.string().min(1),
  VITE_CLERK_JWT_TEMPLATE: z.string().min(1).default("polytrade"),
});

export const env = envSchema.parse(import.meta.env);

if (import.meta.env.PROD) {
  const parsed = new URL(env.VITE_API_URL);
  const loopback = new Set(["localhost", "127.0.0.1", "[::1]"]).has(parsed.hostname);
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback)) {
    throw new Error("VITE_API_URL must use HTTPS outside local loopback development");
  }
}
