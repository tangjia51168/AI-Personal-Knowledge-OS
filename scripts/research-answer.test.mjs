import test from "node:test";
import assert from "node:assert/strict";
import { parseResearchRun, parseSnapshotEvidence } from "../apps/web/src/lib/research-answer.ts";
const id = "00000000-0000-0000-0000-000000000001";
const citation = { id: "R001", note_id: id, revision_id: id, snapshot_id: id, version: 1, title: "人工笔记", provenance: "user_import", content_kind: "article", evidence_key: "E001", quote: "真实原文" };
const run = { id, question: "问题", query: "原文", retrieval: "literal_latest_revisions", status: "succeeded", error_code: null, citations: [citation], answer: { insufficient_evidence: false, statements: [{ text: "解释", citations: ["R001"] }], limitations: ["未核验事实"] } };
test("research contracts bind original question, run, actual citation inventory and empty evidence", () => {
  assert.deepEqual(parseResearchRun(run, { id, question: "问题", query: "原文" }), run);
  assert.equal(parseResearchRun({ ...run, citations: [], answer: { insufficient_evidence: true, statements: [], limitations: ["没有证据"] } }).answer.insufficient_evidence, true);
  assert.deepEqual(parseSnapshotEvidence({ snapshot_id: id, evidence: [{ key: "E001", text: "原文" }], total_segments: 2, partial: true }, id).evidence, [{ key: "E001", text: "原文" }]);
});
test("research refuses malformed, duplicate, fabricated or stale bindings", () => {
  for (const changes of [{ id: "bad" }, { status: "complete" }, { retrieval: "semantic" }, { citations: [citation, citation] }, { citations: [{ ...citation, revision_id: "other" }] }, { citations: [] }, { status: "pending" }, { answer: null }]) assert.throws(() => parseResearchRun({ ...run, ...changes }));
  assert.throws(() => parseResearchRun(run, { question: "其他问题" }));
  assert.throws(() => parseResearchRun(run, { id: "other" }));
  assert.throws(() => parseResearchRun({ ...run, answer: { ...run.answer, statements: [{ text: "捏造", citations: ["R999"] }] } }));
  for (const changes of [{ snapshot_id: "other" }, { partial: false }, { evidence: [{ key: "E001", text: "" }] }, { total_segments: -1 }]) assert.throws(() => parseSnapshotEvidence({ snapshot_id: id, evidence: [{ key: "E001", text: "原文" }], total_segments: 2, partial: true, ...changes }, id));
});
