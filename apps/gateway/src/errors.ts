export class AppError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly code: string,
    message: string,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "AppError";
  }
}

export const unauthorized = (message = "Authentication required") =>
  new AppError(401, "UNAUTHORIZED", message);
export const forbidden = (message = "Forbidden") => new AppError(403, "FORBIDDEN", message);
export const notFound = (message = "Not found") => new AppError(404, "NOT_FOUND", message);
export const conflict = (message: string) => new AppError(409, "CONFLICT", message);
export const validation = (message: string, details?: unknown) =>
  new AppError(400, "VALIDATION_ERROR", message, details);
export const unavailable = (message: string) =>
  new AppError(503, "UPSTREAM_UNAVAILABLE", message);

/**
 * True when an upstream failure definitively did NOT accept the order — a 4xx
 * reply means Polymarket processed the request and refused it, so the safe
 * terminal state is "rejected". Timeouts, 5xx, and network failures are
 * ambiguous (the order may have landed) and must never be treated as rejected.
 */
export function isDefinitiveRejection(error: unknown): boolean {
  if (error instanceof AppError) return error.statusCode >= 400 && error.statusCode < 500;
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" && Number.isInteger(status) && status >= 400 && status < 500;
}
