import test from "node:test";
import assert from "node:assert/strict";
import { parseLearningSchedule } from "../apps/web/src/lib/learning-schedule.ts";

function payload() {
  return { as_of: "2026-11-08T00:00:00Z", until: "2026-11-15T00:00:00Z", period: "7", limit: 100, truncated: false, items: [{ note_id: "00000000-0000-0000-0000-000000000001", title: "个人复习协议夹具", status: "skimmed", reminder_at: "2026-11-09T00:00:00Z", updated_at: "2026-11-08T00:00:00Z" }] };
}

test("agenda contract preserves authorized time range and empty due lists", () => {
  assert.deepEqual(parseLearningSchedule(payload(), "7"), payload());
  const due = { ...payload(), until: payload().as_of, period: "due", items: [] };
  assert.deepEqual(parseLearningSchedule(due, "due"), due);
});

test("agenda never displays wrong period, malformed states, unordered or duplicated notes", () => {
  const data = payload(), item = data.items[0];
  for (const value of [null, {}, { ...data, period: "90" }, { ...data, until: data.as_of }, { ...data, limit: 101 }, { ...data, truncated: true }, { ...data, items: [item, item] }, { ...data, items: [{ ...item, status: "deep_read" }] }, { ...data, items: [{ ...item, reminder_at: "2026-11-08T00:00:00Z" }] }, { ...data, items: [{ ...item, reminder_at: "2026-11-16T00:00:00Z" }] }, { ...data, items: [{ ...item, updated_at: "2026-11-08T00:00:00" }] }, { ...data, items: [item, { ...item, note_id: "00000000-0000-0000-0000-000000000002", reminder_at: "2026-11-08T12:00:00Z" }] }]) {
    assert.throws(() => parseLearningSchedule(value, "7"), /LEARNING_SCHEDULE_INVALID/);
  }
});
