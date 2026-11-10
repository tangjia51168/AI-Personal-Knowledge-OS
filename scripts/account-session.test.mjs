import assert from "node:assert/strict";
import test from "node:test";
import { browserMutationAllowed, sessionDeadline, validSessionToken } from "../apps/web/src/lib/account-session.ts";

test("cookie mutations reject absent marker, cross-site, sibling origin and malformed origin", () => {
  const request = (headers) => new Request("http://localhost/api/account/auth/login", { method: "POST", headers: { Host: "localhost:13000", ...headers } });
  assert.equal(browserMutationAllowed(request({})), false);
  for (const origin of ["null", "https://evil.invalid", "https://localhost:13000", "http://localhost:3000", "http://localhost:13000.evil.invalid", "file://localhost:13000"]) {
    assert.equal(browserMutationAllowed(request({ "X-Knowledge-Request": "1", Origin: origin })), false);
  }
  assert.equal(browserMutationAllowed(request({ "X-Knowledge-Request": "1", Origin: "http://localhost:13000", "Sec-Fetch-Site": "cross-site" })), false);
  assert.equal(browserMutationAllowed(request({ "X-Knowledge-Request": "1", Origin: "http://localhost:13000", "Sec-Fetch-Site": "same-origin" })), true);
  assert.equal(browserMutationAllowed(request({ "X-Knowledge-Request": "1" })), true);
  assert.equal(browserMutationAllowed(request({ "X-Knowledge-Request": "1", "X-Forwarded-Proto": "https", Origin: "https://localhost:13000" })), true);
  assert.equal(browserMutationAllowed(request({ "X-Knowledge-Request": "1", "X-Forwarded-Proto": "https", Origin: "http://localhost:13000" })), false);
});

test("session bounds fail closed for invalid tokens, expired or excessive deadline", () => {
  for (const value of [undefined, "", "a".repeat(42), "a".repeat(44), "!".repeat(43), "a".repeat(42) + "\n"]) assert.equal(validSessionToken(value), false);
  assert.equal(validSessionToken("a_-".repeat(14) + "a"), true);
  const now = Date.parse("2026-11-09T12:00:00Z");
  for (const value of [undefined, "invalid", "2026-11-09T11:59:59Z", "2026-11-10T12:00:00Z"]) assert.equal(sessionDeadline(value, now), null);
  assert.equal(sessionDeadline("2026-11-10T00:00:00Z", now)?.toISOString(), "2026-11-10T00:00:00.000Z");
});
