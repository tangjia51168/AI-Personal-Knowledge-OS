// Mirrors the bounded schedule API; no sample records or inferred review times.
export const schedulePeriods = ["due", "7", "30", "90"] as const;
export type SchedulePeriod = typeof schedulePeriods[number];
export const schedulePeriodNames = { due: "已到期", "7": "未来 7 天", "30": "未来 30 天", "90": "未来 90 天" } as const;
export type LearningSchedule = {
  as_of: string; until: string; period: SchedulePeriod; limit: 100; truncated: boolean;
  items: { note_id: string; title: string; status: "unseen" | "seen" | "skimmed"; reminder_at: string; updated_at: string }[];
};

function invalid(): never { throw new Error("LEARNING_SCHEDULE_INVALID"); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function timestamp(value: unknown): string {
  if (typeof value !== "string" || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) return invalid();
  return value;
}

export function parseLearningSchedule(value: unknown, period: SchedulePeriod): LearningSchedule {
  const data = object(value);
  const as_of = timestamp(data.as_of), until = timestamp(data.until);
  if (data.period !== period || data.limit !== 100 || typeof data.truncated !== "boolean" || !Array.isArray(data.items) || data.items.length > 100) return invalid();
  const start = Date.parse(as_of), end = Date.parse(until);
  if (end - start !== (period === "due" ? 0 : Number(period) * 86400000)) return invalid();
  const seen = new Set<string>();
  let previous = -Infinity;
  const items = data.items.map(value => {
    const item = object(value);
    if (typeof item.note_id !== "string" || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(item.note_id)
      || seen.has(item.note_id) || typeof item.title !== "string" || !["unseen", "seen", "skimmed"].includes(String(item.status))) return invalid();
    seen.add(item.note_id);
    const reminder_at = timestamp(item.reminder_at), updated_at = timestamp(item.updated_at);
    const time = Date.parse(reminder_at);
    if (time > end || (period !== "due" && time <= start) || time < previous) return invalid();
    previous = time;
    return { note_id: item.note_id, title: item.title, status: item.status as "unseen" | "seen" | "skimmed", reminder_at, updated_at };
  });
  if (data.truncated && items.length !== 100) return invalid();
  return { as_of, until, period, limit: 100, truncated: data.truncated, items };
}
