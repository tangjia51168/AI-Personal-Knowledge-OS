"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useRef, useState } from "react";

type Node = { id: string; title: string; version: number; origin: string; distance: number };
type Edge = { id: string; source: string; target: string; method: "manual"; confidence: null; created_by: string; updated_by: string };
type Graph = { root_id: string; depth: number; nodes: Node[]; edges: Edge[]; truncated: boolean; truncation_reasons: string[]; limits: { nodes: number; edges: number } };
type Props = { token: string; space: string; noteId: string; disabled: boolean; onOpen: (id: string) => Promise<void> };

function coordinates(nodes: Node[]) {
  const positions = new Map<string, { x: number; y: number }>();
  for (const distance of [0, 1, 2]) {
    const ring = nodes.filter(node => node.distance === distance);
    ring.forEach((node, index) => {
      const angle = index * 2 * Math.PI / ring.length - Math.PI / 2;
      const radius = distance === 0 ? 0 : distance === 1 ? 145 : 265;
      positions.set(node.id, { x: 300 + Math.cos(angle) * radius, y: 300 + Math.sin(angle) * radius });
    });
  }
  return positions;
}

export default function NoteGraph({ token, space, noteId, disabled, onOpen }: Props) {
  const field = useId();
  const [depth, setDepth] = useState("1");
  const [graph, setGraph] = useState<Graph | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => { controller.current?.abort(); }, []);
  async function load() {
    if (busy || disabled || controller.current) return;
    const request = new AbortController(); controller.current = request;
    setBusy(true); setGraph(null); setMessage("正在读取真实人工链接。");
    try {
      const response = await fetch(`/api/account/spaces/${space}/notes/${noteId}/graph?depth=${depth}`, {
        cache: "no-store", signal: request.signal, headers: { Authorization: `Bearer ${token}` },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "GRAPH_READ_FAILED");
      if (!request.signal.aborted) {
        setGraph(result.data);
        setMessage(`已加载 ${result.data.nodes.length} 个节点、${result.data.edges.length} 条人工链接。`);
      }
    } catch (error) { if (!request.signal.aborted) { setGraph(null); setMessage(`${error instanceof Error ? error.message : "读取失败"}；未展示旧图，可手动重试。`); } }
    finally { if (!request.signal.aborted) { controller.current = null; setBusy(false); } }
  }
  const positions = graph ? coordinates(graph.nodes) : new Map<string, { x: number; y: number }>();
  return <section aria-label="人工链接局部图谱">
    <h4>人工链接局部图谱</h4>
    <p>以当前笔记为中心的一至两跳人工链接；无方向，不表示支持、反驳、因果、事实核验或模型置信度。节点使用读取时的最新标题，与当前查看的历史版本无关。不是全空间知识图谱。</p>
    <label htmlFor={field}>图谱范围</label>{" "}<select id={field} value={depth} disabled={busy || disabled} onChange={event => { setDepth(event.target.value); setGraph(null); setMessage("范围已改变，请重新读取图谱。"); }}><option value="1">一跳人工链接</option><option value="2">两跳人工链接</option></select>{" "}
    <button className="button secondary" disabled={busy || disabled} onClick={() => void load()}>读取局部图谱</button>
    <p role="status" aria-label="局部图谱状态">{message}</p>
    {graph && <>
      <p>当前展示 {graph.depth} 跳范围，最多 {graph.limits.nodes} 节点 / {graph.limits.edges} 边。线条不带箭头；编号对应下方可键盘操作的笔记列表。布局位置与距离不代表语义相似度。</p>
      {graph.truncated && <p role="note">结果已截断：{graph.truncation_reasons.includes("node_limit") && "达到节点上限。"}{graph.truncation_reasons.includes("edge_limit") && "达到边上限。"}未展示内容不代表不存在；可打开其他笔记重新读取局部图谱。</p>}
      {graph.edges.length === 0 && <p>当前范围没有有效人工链接，仅展示中心笔记。</p>}
      <svg role="img" aria-label="人工链接局部图谱示意" viewBox="0 0 600 600" style={{ width: "100%", maxWidth: 600, height: "auto", border: "1px solid currentColor" }}>
        <title>人工链接局部图谱，详细标题与链接见下方列表</title>
        {graph.edges.map(edge => { const a = positions.get(edge.source)!; const b = positions.get(edge.target)!; return <line key={edge.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="currentColor" opacity="0.35" />; })}
        {graph.nodes.map((node, index) => { const point = positions.get(node.id)!; return <g key={node.id}><title>{node.title} · 最新版本 {node.version} · {node.distance} 跳</title><circle cx={point.x} cy={point.y} r={node.distance === 0 ? 23 : 17} fill="Canvas" stroke="currentColor" strokeWidth={node.distance === 0 ? 3 : 1} /><text x={point.x} y={point.y + 5} fill="currentColor" textAnchor="middle" fontSize="14">{index + 1}</text></g>; })}
      </svg>
      <ol aria-label="图谱笔记节点">{graph.nodes.map(node => <li key={node.id} style={{ overflowWrap: "anywhere" }}><span>{node.title} · 最新版本 {node.version} · {node.distance === 0 ? "中心笔记" : `${node.distance} 跳`}</span>{" "}<button className="button secondary" disabled={busy || disabled || node.id === noteId} aria-label={`从图谱打开笔记：${node.title}`} onClick={() => void onOpen(node.id)}>打开笔记</button></li>)}</ol>
      <details><summary>人工链接明细（{graph.edges.length}）</summary><ul aria-label="图谱人工链接">{graph.edges.map(edge => <li key={edge.id} style={{ overflowWrap: "anywhere" }}>节点 {graph.nodes.findIndex(node => node.id === edge.source) + 1} — 节点 {graph.nodes.findIndex(node => node.id === edge.target) + 1} · 人工 · 创建者 {edge.created_by} · 最后操作者 {edge.updated_by}</li>)}</ul></details>
    </>}
  </section>;
}
