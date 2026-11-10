import test from "node:test";
import assert from "node:assert/strict";
import { parseLearningAnalysis } from "../apps/web/src/lib/learning-analysis.ts";

const payload = () => ({ as_of: "2026-11-08T00:00:00+00:00", total_notes: 4, recorded_notes: 3, unrecorded_notes: 1, states: { unseen: 1, seen: 1, skimmed: 1, deep_read: 1 }, completed_notes: 3, completed_last_7_days: 2, completed_last_30_days: 3, reminders: { due: 1, upcoming: 1 } });

test("learning analysis preserves bounded real counters", () => {
  assert.deepEqual(parseLearningAnalysis(payload()), payload());
  assert.throws(() => parseLearningAnalysis({ ...payload(), completed_last_7_days: 4 }), /LEARNING_ANALYSIS_INVALID/);
  assert.throws(() => parseLearningAnalysis({ ...payload(), total_notes: 5 }), /LEARNING_ANALYSIS_INVALID/);
});
