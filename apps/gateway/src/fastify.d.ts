import "fastify";

import type { Principal } from "./types.js";

declare module "fastify" {
  interface FastifyRequest {
    principal?: Principal;
  }
}
