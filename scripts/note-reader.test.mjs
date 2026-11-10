import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { readerDocument, readerEvidence, recallCards } from "../apps/web/src/lib/note-reader.ts";

const item = { text: "协议夹具，无模型或平台请求", evidence_keys: ["E001"] };
const document = { overview: item, sections: [{ kind: "core_claims", items: [item] }], limitations: ["尚未核验"], coverage: { total_segments: 2, provided_segments: 1, cited_segments: 1, partial: true } };
const exported = { schema: "knowledge-note-export-v1", note_id: "note", revision: { id: "revision" }, source: { original_url: "https://example.com/protocol" }, snapshot: { id: "snapshot", provenance: "user_import", limitations: ["未核验"] }, evidence: [{ key: "E001", quote: "已保存引文" }] };
const evidence = value => readerEvidence(value, "note", "revision", "snapshot", ["E001"]);

test("long-analysis stages are bounded, validated and retain their evidence without changing recall scope", () => {
  const stage = { overview: item, sections: document.sections, limitations: document.limitations };
  const value = { ...document, prompt_version: "evidence-summary-v2", coverage: {total_segments:2,provided_segments:2,cited_segments:1,partial:false}, analysis:{method:"bounded-map-synthesis-v1",stages:[stage,stage]} };
  const parsed = readerDocument(value);
  assert.equal(parsed.stages.length, 2);
  assert.equal(recallCards(parsed, evidence(exported)).length, 2);
  for (const analysis of [{method:"invalid",stages:[stage,stage]}, {method:"bounded-map-synthesis-v1",stages:[stage]}, {method:"bounded-map-synthesis-v1",stages:Array(9).fill(stage)}, {method:"bounded-map-synthesis-v1",stages:[stage,{...stage,overview:{text:"bad",evidence_keys:[]}}]}]) assert.equal(readerDocument({...value,analysis}),null);
});

test("recall uses only parsed fixed-revision items and their exact evidence, excluding personal notes", () => {
  const parsed = readerDocument({ ...document, personal_notes: "不能当成来源答案" });
  const source = evidence(exported);
  const deck = recallCards(parsed, source);
  assert.deepEqual(deck.map(card => card.id), ["overview", "core_claims-0"]);
  assert.equal(deck[1].reference.text, item.text);
  assert.deepEqual(deck[1].quotes, source.quotes);
  assert.doesNotMatch(JSON.stringify(deck), /不能当成来源答案/);
  deck[0].reference.evidence_keys.push("E002");
  deck[0].quotes[0].quote = "修改本地卡片";
  assert.deepEqual(parsed.overview.evidence_keys, ["E001"]);
  assert.equal(source.quotes[0].quote, "已保存引文");
  assert.throws(() => recallCards(parsed, { ...source, quotes: [] }), /RECALL_EVIDENCE_INVALID/);
});

test("maximum deck is bounded to 31 cards and missing evidence is never silently omitted", () => {
  const sections = ["core_claims", "steps", "examples", "numbers", "takeaways"].map(kind => ({ kind, items: Array.from({ length: 6 }, () => ({ ...item, evidence_keys: ["E001", "E002"] })) }));
  const parsed = readerDocument({ ...document, sections });
  const source = { ...evidence(exported), quotes: [{ key: "E001", quote: "原文一" }, { key: "E002", quote: "原文二" }] };
  assert.equal(recallCards(parsed, source).length, 31);
  assert.equal(new Set(recallCards(parsed, source).map(card => card.id)).size, 31);
  assert.throws(() => recallCards(parsed, { ...source, quotes: source.quotes.slice(0, 1) }), /RECALL_EVIDENCE_INVALID/);
});
test("reader preserves structured paragraphs, range, personal notes and absent sections without invention", () => {
  const parsed = readerDocument(document);
  assert.equal(parsed.overview.text, item.text);
  assert.equal(parsed.sections.length, 1);
  assert.equal(parsed.coverage.partial, true);
  assert.equal(parsed.personal, null);
  assert.equal(readerDocument({ ...document, personal_notes: "个人理解" }).personal, "个人理解");
  assert.deepEqual(document.sections[0].items[0], item);
});
test("reader rejects malformed, inherited, repeated, oversized sections or references", () => {
  for (const value of [null, [], {}, { ...document, sections: [] }, { ...document, sections: [...document.sections, ...document.sections] }, { ...document, sections: [{ kind: "__proto__", items: [item] }] }, { ...document, overview: { ...item, evidence_keys: ["E001", "E001"] } }, { ...document, overview: { ...item, evidence_keys: ["javascript:"] } }, { ...document, personal_notes: "x".repeat(4001) }, { ...document, overview: { ...item, text: "x".repeat(1201) } }]) assert.equal(readerDocument(value), null);
});
test("range counters are not guessed and inconsistent coverage is rejected", () => {
  for (const coverage of [{ ...document.coverage, total_segments: -1 }, { ...document.coverage, cited_segments: 2 }, { ...document.coverage, partial: false }, { ...document.coverage, provided_segments: 1.5 }, { ...document.coverage, total_segments: 81 }]) assert.equal(readerDocument({ ...document, coverage }), null);
});
test("evidence is bound to exact note, revision, snapshot and required keys", () => {
  assert.equal(evidence(exported).quotes[0].quote, "已保存引文");
  for (const value of [{ ...exported, note_id: "other" }, { ...exported, revision: { id: "other" } }, { ...exported, snapshot: { ...exported.snapshot, id: "other" } }, { ...exported, evidence: [] }, { ...exported, evidence: [...exported.evidence, ...exported.evidence] }, { ...exported, evidence: [{ key: "E001", quote: "x".repeat(801) }] }, { ...exported, schema: "other" }]) assert.equal(evidence(value), null);
});
test("hostile strings remain plain text, never become HTML, links or executable content", () => {
  const hostile = '<img src=x onerror=alert(1)> https://example.com';
  assert.equal(readerDocument({ ...document, overview: { ...item, text: hostile } }).overview.text, hostile);
  assert.equal(evidence({ ...exported, source: { original_url: "javascript:alert(1)" }, evidence: [{ key: "E001", quote: hostile }] }).quotes[0].quote, hostile);
  const source = readFileSync(new URL("../apps/web/src/app/account/note-reader.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|localStorage|sessionStorage|<a\b|<img\b|eval\(/);
  assert.match(source, /active\.current\?\.abort/);
  assert.match(source, /setEvidence\(null\)/);
});
