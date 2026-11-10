import test from "node:test";
import assert from "node:assert/strict";
import { conceptCards } from "../apps/web/src/lib/concept-cards.ts";

const id = "00000000-0000-0000-0000-000000000001";
const row = { id, note_id: id, revision_id: id, snapshot_id: id, name: "定义", definition: "<script>人工解释</script>", evidence_key: "E001", quote: "原文", method: "manual", confidence: null, created_by: id, created_at: "2026-11-09T00:00:00Z" };
test("concept cards preserve inert text, empty state and exact revision", () => {
  assert.deepEqual(conceptCards([], id, id, id), []);
  assert.equal(conceptCards([row], id, id, id)[0].definition, row.definition);
});
test("concept cards refuse stale bindings, duplicates, malformed attribution and oversized inventory", () => {
  for (const changes of [{ note_id: "foreign" }, { revision_id: "foreign" }, { snapshot_id: "foreign" }, { method: "ai" }, { confidence: 0.9 }, { quote: "" }, { name: "a".repeat(61) }, { evidence_key: "E１２３" }, { created_by: "invalid" }, { created_at: "invalid" }]) {
    assert.throws(() => conceptCards([{ ...row, ...changes }], id, id, id));
  }
  assert.throws(() => conceptCards([row, row], id, id, id));
  assert.throws(() => conceptCards(Array(21).fill(row), id, id, id));
});
