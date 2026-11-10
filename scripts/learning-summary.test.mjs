import test from "node:test";
import assert from "node:assert/strict";
import { parseLearningSummary } from "../apps/web/src/lib/learning-summary.ts";

function payload() {
  return { as_of: "2026-11-08T00:00:00+00:00", total_notes: 4, recorded_notes: 3, unrecorded_notes: 1, states: { unseen: 1, seen: 1, skimmed: 1, deep_read: 1 }, reminders: { due: 1, upcoming: 1 } };
}

test("learning summary preserves real counts and empty spaces without percentages", () => {
  assert.deepEqual(parseLearningSummary(payload()), payload());
  const empty = { as_of: payload().as_of, total_notes: 0, recorded_notes: 0, unrecorded_notes: 0, states: { unseen: 0, seen: 0, skimmed: 0, deep_read: 0 }, reminders: { due: 0, upcoming: 0 } };
  assert.deepEqual(parseLearningSummary(empty), empty);
});

test("malformed or contradictory counts never become displayed progress", () => {
  for (const change of [null, [], {}, { ...payload(), as_of: "2026-11-08T00:00:00" }, { ...payload(), total_notes: -1 }, { ...payload(), recorded_notes: 4 }, { ...payload(), total_notes: "4" }, { ...payload(), reminders: { due: 3, upcoming: 0 } }, { ...payload(), unrecorded_notes: 3 }, { ...payload(), states: { unseen: 0, seen: 1, skimmed: 1, deep_read: 2 } }, { ...payload(), total_notes: Infinity }, { ...payload(), recorded_notes: 2.1 }]) {
    assert.throws(() => parseLearningSummary(change), /LEARNING_SUMMARY_INVALID/);
  }
});
