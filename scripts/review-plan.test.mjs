import test from "node:test";
import assert from "node:assert/strict";
import { reviewPlan } from "../apps/web/src/lib/review-plan.ts";

test("plan groups explicit selection in order and handles month/year boundaries", () => {
  process.env.TZ = "UTC";
  assert.deepEqual(reviewPlan(5, "2026-12-31T10:30", 2, 2), [
    "2026-12-31T10:30:00.000Z", "2026-12-31T10:30:00.000Z",
    "2027-01-02T10:30:00.000Z", "2027-01-02T10:30:00.000Z", "2027-01-04T10:30:00.000Z",
  ]);
  assert.equal(reviewPlan(4, "2026-01-01T10:00", 1, 30).at(-1), "2026-04-01T10:00:00.000Z");
});

test("local calendar allocation preserves wall clock across DST and rejects gaps", () => {
  process.env.TZ = "America/Los_Angeles";
  assert.deepEqual(reviewPlan(2, "2027-03-13T10:00", 1, 1), ["2027-03-13T18:00:00.000Z", "2027-03-14T17:00:00.000Z"]);
  assert.throws(() => reviewPlan(2, "2027-03-13T02:30", 1, 1));
  assert.throws(() => reviewPlan(1, "2027-03-14T02:30", 1, 1));
  assert.equal(reviewPlan(1, "2026-11-01T01:30", 1, 1)[0], "2026-11-01T08:30:00.000Z");
});

test("plan rejects bad dates, noninteger capacity, huge spans and malformed bounds", () => {
  process.env.TZ = "UTC";
  for (const args of [
    [0, "2026-01-01T10:00", 1, 1], [21, "2026-01-01T10:00", 1, 1],
    [2, "2026-02-30T10:00", 1, 1], [2, "2026-01-01", 1, 1],
    [2, "2026-01-01T10:00", 0, 1], [2, "2026-01-01T10:00", 1.5, 1],
    [2, "2026-01-01T10:00", 21, 1], [2, "2026-01-01T10:00", 1, 31],
    [20, "2026-01-01T10:00", 1, 5], [2, "2026-01-01T10:00", 1, NaN],
    [2, "1960-01-01T10:00", 1, 1], [2, "9999-12-31T10:00", 1, 1],
  ]) assert.throws(() => reviewPlan(...args));
  assert.equal(reviewPlan(20, "2026-01-01T10:00", 20, 30).length, 20);
});
