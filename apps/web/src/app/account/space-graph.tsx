"use client";

import { useRef, useState } from "react";
import { boundGraphViewport, filterSpaceGraph, initialGraphViewport, type GraphConnection, type SpaceGraph } from "@/lib/knowledge-explorer";

type Props = { graph: SpaceGraph; disabled: boolean; onOpen: (note: string, revision: string) => void };

export default function SpaceGraphView({ graph, disabled, onOpen }: Props) {
  const [query, setQuery] = useState(""), [connection, setConnection] = useState<GraphConnection>("all");
  const [viewport, setViewport] = useState(initialGraphViewport);
  const drag = useRef<{ id: number; x: number; y: number; width: number; height: number } | null>(null);
  const filtered = filterSpaceGraph(graph, query, connection);
  // Positions and numbers stay fixed during filtering; geometry has no semantic meaning.
  const positions = new Map(graph.nodes.map((node, index) => { const angle = 2 * Math.PI * index / graph.nodes.length; return [node.id, { x: 300 + 260 * Math.cos(angle), y: 300 + 260 * Math.sin(angle), number: index + 1 }]; }));
  const transform = `translate(${300 + viewport.x} ${300 + viewport.y}) scale(${viewport.zoom}) translate(-300 -300)`;
  function move(x: number, y: number) { setViewport(old => boundGraphViewport({ ...old, x: old.x + x, y: old.y + y })); }
  function reset() { drag.current = null; setQuery(""); setConnection("all"); setViewport(initialGraphViewport); }
  return <div className="space-graph-view">
    {graph.truncated && <p role="note">空间图已截断：{graph.truncation_reasons.includes("node_limit") && "节点超过30篇；"}{graph.truncation_reasons.includes("edge_limit") && "所选节点间链接超过60条；"}可使用检索及笔记局部图谱继续探索。</p>}
    {graph.nodes.length === 0 ? <p>当前空间暂无有修订的笔记。</p> : <>
      <p id="space-graph-help">搜索和筛选仅作用于已读取的节点；连接状态仅依据已返回链接，截断时不能据此认定全库孤立。拖动画布平移，也可用方向键或按钮；通过下方笔记列表打开修订。</p>
      <div className="space-graph-controls"><label>图中标题搜索<input aria-label="图中标题搜索" maxLength={120} value={query} disabled={disabled} onChange={event => setQuery(event.target.value)} /></label><label>图中连接筛选<select aria-label="图中连接筛选" value={connection} disabled={disabled} onChange={event => setConnection(event.target.value as GraphConnection)}><option value="all">全部节点</option><option value="linked">已返回链接的节点</option><option value="isolated">未返回链接的节点</option></select></label></div>
      <div className="space-graph-controls" role="group" aria-label="空间图视图控制">
        <button className="button secondary" disabled={disabled || viewport.zoom >= 3} onClick={() => setViewport(old => boundGraphViewport({ ...old, zoom: old.zoom + 0.25 }))}>放大图谱</button>
        <button className="button secondary" disabled={disabled || viewport.zoom <= 0.5} onClick={() => setViewport(old => boundGraphViewport({ ...old, zoom: old.zoom - 0.25 }))}>缩小图谱</button>
        <button className="button secondary" disabled={disabled} onClick={() => move(-40, 0)}>向左平移</button><button className="button secondary" disabled={disabled} onClick={() => move(40, 0)}>向右平移</button>
        <button className="button secondary" disabled={disabled} onClick={() => move(0, -40)}>向上平移</button><button className="button secondary" disabled={disabled} onClick={() => move(0, 40)}>向下平移</button>
        <button className="button secondary" disabled={disabled} onClick={reset}>重置图谱视图</button>
      </div>
      <p role="status" aria-label="空间图视图状态">显示 {filtered.nodes.length} / {graph.nodes.length} 个已读取节点、{filtered.edges.length} / {graph.edges.length} 条已读取链接；缩放 {Math.round(viewport.zoom * 100)}%。</p>
      {filtered.nodes.length === 0 && <p>当前图中没有匹配节点；可重置筛选或搜索全部知识内容。</p>}
      <svg role="img" aria-label="空间人工关系示意" aria-describedby="space-graph-help" tabIndex={disabled ? -1 : 0} viewBox="0 0 600 600"
        onKeyDown={event => { if (disabled) return; const delta = { ArrowLeft: [-40, 0], ArrowRight: [40, 0], ArrowUp: [0, -40], ArrowDown: [0, 40] }[event.key]; if (delta) { event.preventDefault(); move(delta[0], delta[1]); } }}
        onPointerDown={event => { if (disabled || !event.isPrimary || event.button !== 0) return; const rect = event.currentTarget.getBoundingClientRect(); if (!rect.width || !rect.height) return; event.currentTarget.setPointerCapture(event.pointerId); drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, width: rect.width, height: rect.height }; }}
        onPointerMove={event => { const start = drag.current; if (disabled || !start || start.id !== event.pointerId) return; move((event.clientX - start.x) * 600 / start.width, (event.clientY - start.y) * 600 / start.height); drag.current = { ...start, x: event.clientX, y: event.clientY }; }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
        <title>空间人工关系图，编号与下方笔记列表对应；位置与线条不表示语义关系</title>
        <g transform={transform}>
          {filtered.edges.map(edge => { const a = positions.get(edge.source)!, b = positions.get(edge.target)!; return <line key={edge.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="currentColor" opacity="0.4" />; })}
          {filtered.nodes.map(node => { const p = positions.get(node.id)!; return <g key={node.id} className="graph-node"><title>{node.title}</title><circle cx={p.x} cy={p.y} r="23" fill="currentColor" opacity="0.1" stroke="none" /><circle cx={p.x} cy={p.y} r="17" fill="currentColor" opacity="0.8" /><text x={p.x} y={p.y + 5} textAnchor="middle" fill="white">{p.number}</text></g>; })}
        </g>
      </svg>
      <ol aria-label="空间图笔记">{filtered.nodes.map(node => <li key={node.id} value={positions.get(node.id)!.number}>{node.title} · 版本 {node.version}{" "}<button className="button secondary" disabled={disabled} onClick={() => onOpen(node.id, node.revision_id)}>打开图中修订</button></li>)}</ol>
      <details><summary>查看空间人工链接（{filtered.edges.length}）</summary><ul>{filtered.edges.map(edge => <li key={edge.id}>节点 {positions.get(edge.source)!.number} — {positions.get(edge.target)!.number} · 人工，无方向</li>)}</ul></details>
    </>}
  </div>;
}
