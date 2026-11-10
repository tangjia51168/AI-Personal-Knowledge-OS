// The API remains the authority for expiration and revocation. Never log values.
export const sessionCookie = "knowledge_session";
export const browserHeader = "X-Knowledge-Request";

export function validSessionToken(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{43}$/.test(value);
}

export function browserMutationAllowed(request: Request): boolean {
  if (request.headers.get(browserHeader) !== "1") return false;
  if (request.headers.get("sec-fetch-site") === "cross-site") return false;
  const origin = request.headers.get("origin");
  if (!origin) return true; // Non-browser clients must still provide the custom header.
  try {
    const url = new URL(origin);
    const protocol = request.headers.get("x-forwarded-proto") === "https" ? "https:" : new URL(request.url).protocol;
    return url.protocol === protocol && url.host === request.headers.get("host");
  } catch { return false; }
}

export function sessionDeadline(value: unknown, now = Date.now()): Date | null {
  if (typeof value !== "string") return null;
  const expiry = new Date(value);
  const remaining = expiry.getTime() - now;
  return remaining > 0 && remaining <= 12 * 60 * 60 * 1000 + 60000 ? expiry : null;
}
