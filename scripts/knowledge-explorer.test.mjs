import assert from "node:assert/strict";
import test from "node:test";
import { boundGraphViewport, filterSpaceGraph, parseSearchPage, parseSpaceGraph } from "../apps/web/src/lib/knowledge-explorer.ts";
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
const hit = { note_id: id(1), revision_id: id(2), title: "协议", version: 1, origin: "user_edit", matched_field: "evidence", snippet: "原文" };
const node = { id: id(1), revision_id: id(2), title: "协议", version: 1 };
const graph = { nodes: [node], edges: [], limits: { nodes: 30, edges: 60 }, truncated: false, truncation_reasons: [] };

test("search parser binds results to revisions and rejects duplicates, overflow and invalid cursor", () => {
  assert.deepEqual(parseSearchPage({ data: [hit], next_cursor: id(1) }).data, [hit]);
  for (const value of [null, { data: [hit, hit], next_cursor: null }, { data: [hit], next_cursor: id(3) },
    ...[{ snippet: "😀".repeat(241) }, { version: 0 }, { matched_field: "ai" }, { revision_id: "invalid" }].map(patch => ({ data: [{ ...hit, ...patch }], next_cursor: null }))]) assert.throws(() => parseSearchPage(value));
});
test("space graph parser rejects dangling, self, duplicate and inferred links and false completeness", () => {
  assert.deepEqual(parseSpaceGraph(graph), graph);
  const edge = { id: id(3), source: id(1), target: id(4), method: "manual", confidence: null };
  for (const value of [{ ...graph, truncated: true }, { ...graph, edges: [edge] }, { ...graph, nodes: [node, node] },
    { ...graph, nodes: [node, { ...node, id: id(4) }], edges: [{ ...edge, method: "semantic" }] }]) assert.throws(() => parseSpaceGraph(value));
});

test("graph filtering retains fixed revision IDs, sample completeness and only edges between matches", () => {
  const source = { ...graph, truncated: true, truncation_reasons: ["node_limit"], nodes: [node, { ...node, id: id(4), title: "Beta" }, { ...node, id: id(5), title: "孤立" }], edges: [{ id: id(8), source: id(1), target: id(4), method: "manual", confidence: null }] };
  assert.equal(filterSpaceGraph(source, " beta ", "all").nodes[0].id, id(4));
  assert.equal(filterSpaceGraph(source, "beta", "all").edges.length, 0);
  assert.equal(filterSpaceGraph(source, "", "linked").edges.length, 1);
  const isolated = filterSpaceGraph(source, "", "isolated");
  assert.deepEqual(isolated.nodes.map(n => n.id), [id(5)]);
  assert.equal(isolated.truncated, true);
  assert.deepEqual(isolated.truncation_reasons, ["node_limit"]);
  assert.equal(source.nodes.length, 3);
  assert.equal(source.edges.length, 1);
});

test("graph pan and zoom stay bounded and reject nonfinite coordinates", () => {
  assert.deepEqual(boundGraphViewport({ x: -999, y: 999, zoom: 99 }), { x: -600, y: 600, zoom: 3 });
  assert.equal(boundGraphViewport({ x: 0, y: 0, zoom: -9 }).zoom, 0.5);
  for (const invalid of [NaN, Infinity, -Infinity]) assert.deepEqual(boundGraphViewport({ x: invalid, y: 1, zoom: 1 }), { x: 0, y: 0, zoom: 1 });
});
