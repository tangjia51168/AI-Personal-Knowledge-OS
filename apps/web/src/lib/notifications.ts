export type NotificationPreference = { version: number; enabled: boolean; email: boolean };
export type NotificationDelivery = { id: string; kind: "due" | "test"; note_id: string | null; reminder_at: string | null; email_state: "not_requested" | "pending" | "dispatched" | "smtp_accepted" | "unknown" | "cancelled"; created_at: string; read_at: string | null };
export type NotificationPage = { preference: NotificationPreference; email_verified: boolean; smtp_configured: boolean; data: NotificationDelivery[]; has_more: boolean };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const timestamp = (value: unknown): value is string => typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value));
export const notificationEmailLabels = { not_requested: "仅站内通知", pending: "等待邮件尝试", dispatched: "已开始发送，结果待确认", smtp_accepted: "SMTP 已接受，未证明收件箱送达", unknown: "结果不确定，禁止自动重发", cancelled: "邮件尝试已取消" };
export function parseNotificationPage(value: unknown): NotificationPage {
  const page = value as Partial<NotificationPage> | null;
  const p = page?.preference;
  if (!page || !p || !Number.isSafeInteger(p.version) || p.version < 0 || typeof p.enabled !== "boolean" || typeof p.email !== "boolean" || !p.enabled && p.email || typeof page.email_verified !== "boolean" || typeof page.smtp_configured !== "boolean" || typeof page.has_more !== "boolean" || !Array.isArray(page.data) || page.data.length > 50) throw new Error("INVALID_NOTIFICATION_RESPONSE");
  for (const row of page.data) {
    if (!row || typeof row.id !== "string" || !uuid.test(row.id) || !["due", "test"].includes(row.kind) || !Object.hasOwn(notificationEmailLabels, row.email_state) || !timestamp(row.created_at) || !(row.read_at === null || timestamp(row.read_at)) || (row.kind === "test" ? row.note_id !== null || row.reminder_at !== null : typeof row.note_id !== "string" || !uuid.test(row.note_id) || !timestamp(row.reminder_at))) throw new Error("INVALID_NOTIFICATION_RESPONSE");
  }
  if (new Set(page.data.map(row => row.id)).size !== page.data.length) throw new Error("INVALID_NOTIFICATION_RESPONSE");
  return page as NotificationPage;
}
