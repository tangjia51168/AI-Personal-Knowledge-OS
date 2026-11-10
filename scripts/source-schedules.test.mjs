import test from "node:test";
import assert from "node:assert/strict";
import { parseSchedule, parseSchedulePage } from "../apps/web/src/lib/source-schedules.ts";
const id = "00000000-0000-0000-0000-000000000001";
const row = { id, source_id: id, interval_minutes: 1440, active: false, version: 2, next_run_at: "2026-11-09T00:00:00Z", last_error: "COLLECTOR_DISABLED" };
test("schedule response preserves disabled/empty state and bounded actual plans", () => {
  assert.deepEqual(parseSchedule(row), row);
  assert.deepEqual(parseSchedulePage({ collector_enabled: false, schedules: [] }), { collector_enabled: false, schedules: [] });
});
test("schedule response rejects duplicate inventory, invalid identity and false activation", () => {
  for (const changes of [{ id: "invalid" }, { interval_minutes: 0 }, { interval_minutes: "1440" }, { active: 1 }, { version: 0 }, { next_run_at: "bad" }, { last_error: "private secret text" }]) assert.throws(() => parseSchedule({ ...row, ...changes }));
  assert.throws(() => parseSchedulePage({ collector_enabled: false, schedules: [row, row] }));
});
