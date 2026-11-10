export type SourceSchedule = { id: string; source_id: string; interval_minutes: number; active: boolean; version: number; next_run_at: string; last_error: string | null };
export type SchedulePage = { collector_enabled: boolean; schedules: SourceSchedule[] };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function parseSchedule(value: unknown): SourceSchedule {
  const row = value as Partial<SourceSchedule> | null;
  if (!row || typeof row !== "object" || Array.isArray(row)
    || typeof row.id !== "string" || !uuid.test(row.id) || typeof row.source_id !== "string" || !uuid.test(row.source_id)
    || !Number.isSafeInteger(row.interval_minutes) || row.interval_minutes! < 360 || row.interval_minutes! > 10080
    || typeof row.active !== "boolean" || !Number.isSafeInteger(row.version) || row.version! < 1
    || typeof row.next_run_at !== "string" || !Number.isFinite(Date.parse(row.next_run_at))
    || !(row.last_error === null || typeof row.last_error === "string" && /^[A-Z0-9_]{1,64}$/.test(row.last_error))) throw new Error("INVALID_SOURCE_SCHEDULE");
  return row as SourceSchedule;
}
export function parseSchedulePage(value: unknown): SchedulePage {
  const page = value as Partial<SchedulePage> | null;
  if (!page || typeof page.collector_enabled !== "boolean" || !Array.isArray(page.schedules) || page.schedules.length > 20) throw new Error("INVALID_SOURCE_SCHEDULE");
  const rows = page.schedules.map(parseSchedule);
  if (new Set(rows.map(row => row.id)).size !== rows.length || new Set(rows.map(row => row.source_id)).size !== rows.length) throw new Error("INVALID_SOURCE_SCHEDULE");
  return { collector_enabled: page.collector_enabled, schedules: rows };
}
