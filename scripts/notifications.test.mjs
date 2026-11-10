import test from "node:test";
import assert from "node:assert/strict";
import { parseNotificationPage } from "../apps/web/src/lib/notifications.ts";
const row = { id: "00000000-0000-0000-0000-000000000001", kind: "test", note_id: null, reminder_at: null, email_state: "unknown", created_at: "2026-11-10T00:00:00Z", read_at: null };
const page = { preference: { version: 1, enabled: true, email: false }, email_verified: false, smtp_configured: false, data: [row], has_more: false };
test("notification receipts preserve uncertainty without inventing delivered state", () => {
  assert.equal(parseNotificationPage(page).data[0].email_state, "unknown");
  for (const invalid of [null, {}, { ...page, data: [row, row] }, { ...page, preference: { ...page.preference, enabled: false, email: true } }, { ...page, has_more: "false" }, { ...page, data: [{ ...row, email_state: "delivered" }] }, { ...page, data: [{ ...row, kind: "due" }] }, { ...page, data: [{ ...row, read_at: "invalid" }] }, { ...page, data: Array.from({ length: 51 }, () => row) }]) assert.throws(() => parseNotificationPage(invalid), /INVALID_NOTIFICATION_RESPONSE/);
});
