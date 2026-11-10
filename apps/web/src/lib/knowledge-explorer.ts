export const searchFieldNames = { title: "标题", body: "笔记正文", evidence: "原文证据", annotation: "批注" };
export type SearchHit = { note_id: string; revision_id: string; title: string; version: number; origin: "ai_generated" | "user_edit"; matched_field: keyof typeof searchFieldNames; snippet: string };
export type SearchPage = { data: SearchHit[]; next_cursor: string | null };
export type SpaceGraph = { nodes: { id: string; revision_id: string; title: string; version: number }[]; edges: { id: string; source: string; target: string; method: "manual"; confidence: null }[]; truncated: boolean; truncation_reasons: string[]; limits: { nodes: number; edges: number } };
export type GraphConnection = "all" | "linked" | "isolated";
export type GraphViewport = { x: number; y: number; zoom: number };
export const initialGraphViewport: GraphViewport = { x: 0, y: 0, zoom: 1 };

// Filter only the loaded sample. Retain its completeness metadata and original IDs.
export function filterSpaceGraph(graph: SpaceGraph, query: string, connection: GraphConnection): SpaceGraph {
  const linked = new Set(graph.edges.flatMap(edge => [edge.source, edge.target]));
  const q = query.trim().toLowerCase();
  const nodes = graph.nodes.filter(node => node.title.toLowerCase().includes(q)
    && (connection === "all" || linked.has(node.id) === (connection === "linked")));
  const ids = new Set(nodes.map(node => node.id));
  return { ...graph, nodes, edges: graph.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)) };
}

export function boundGraphViewport(value: GraphViewport): GraphViewport {
  if (![value.x, value.y, value.zoom].every(Number.isFinite)) return { ...initialGraphViewport };
  return { x: Math.max(-600, Math.min(600, value.x)), y: Math.max(-600, Math.min(600, value.y)), zoom: Math.max(0.5, Math.min(3, value.zoom)) };
}
const uuid = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const version = (v: unknown) => Number.isSafeInteger(v) && Number(v) > 0;
const title = (v: unknown) => typeof v === "string" && Array.from(v).length <= 200;

export function parseSearchPage(value: unknown): SearchPage {
  if (!object(value) || !Array.isArray(value.data) || value.data.length > 20 || !(value.next_cursor === null || uuid(value.next_cursor))) throw new Error("检索响应无效");
  const ids = new Set<string>();
  for (const hit of value.data) {
    if (!object(hit) || !uuid(hit.note_id) || ids.has(hit.note_id) || !uuid(hit.revision_id) || !version(hit.version) || !title(hit.title)
      || !["ai_generated", "user_edit"].includes(String(hit.origin)) || !Object.hasOwn(searchFieldNames, String(hit.matched_field))
      || typeof hit.snippet !== "string" || Array.from(hit.snippet).length > 240) throw new Error("检索响应无效");
    ids.add(hit.note_id);
  }
  if (value.next_cursor !== null && value.next_cursor !== value.data.at(-1)?.note_id) throw new Error("检索分页无效");
  return value as unknown as SearchPage;
}

export function parseSpaceGraph(value: unknown): SpaceGraph {
  if (!object(value) || !Array.isArray(value.nodes) || !Array.isArray(value.edges) || !object(value.limits)
    || value.limits.nodes !== 30 || value.limits.edges !== 60 || value.nodes.length > 30 || value.edges.length > 60
    || typeof value.truncated !== "boolean" || !Array.isArray(value.truncation_reasons)
    || value.truncation_reasons.some(reason => !["node_limit", "edge_limit"].includes(reason))
    || new Set(value.truncation_reasons).size !== value.truncation_reasons.length || value.truncated !== (value.truncation_reasons.length > 0)) throw new Error("空间图响应无效");
  const nodes = new Set<string>(), edges = new Set<string>(), pairs = new Set<string>();
  for (const node of value.nodes) {
    if (!object(node) || !uuid(node.id) || nodes.has(node.id) || !uuid(node.revision_id) || !title(node.title) || !version(node.version)) throw new Error("空间图节点无效");
    nodes.add(node.id);
  }
  for (const edge of value.edges) {
    if (!object(edge) || !uuid(edge.id) || edges.has(edge.id) || typeof edge.source !== "string" || typeof edge.target !== "string"
      || !nodes.has(edge.source) || !nodes.has(edge.target) || edge.source === edge.target || edge.method !== "manual" || edge.confidence !== null) throw new Error("空间图链接无效");
    const pair = [edge.source, edge.target].sort().join("/");
    if (pairs.has(pair)) throw new Error("空间图链接重复");
    pairs.add(pair); edges.add(edge.id);
  }
  return value as unknown as SpaceGraph;
}
