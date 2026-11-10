"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { parseSearchPage, parseSpaceGraph, searchFieldNames, type SearchPage, type SpaceGraph } from "@/lib/knowledge-explorer";
import { boundedFilters, ContentPagination, PlatformOptions } from "./content-list";
import { type KnowledgeLabel } from "./note-labels";
import RevisionView, { type SavedRevision } from "./revision-view";
import SpaceGraphView from "./space-graph";
import SemanticSearch from "./semantic-search";

const empty = { q: "", scope: "all", platform: "", origin: "", label_id: "" };
type Props = { token: string; space: string; disabled: boolean; labels: KnowledgeLabel[] };

export default function KnowledgeExplorer({ token, space, disabled, labels }: Props) {
  const [filters, setFilters] = useState(empty), [applied, setApplied] = useState(empty);
  const [result, setResult] = useState<SearchPage | null>(null), [graph, setGraph] = useState<SpaceGraph | null>(null);
  const [pages, setPages] = useState<(string | null)[]>([null]);
  const [opened, setOpened] = useState<{ note: string; revision: SavedRevision } | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("输入关键词搜索已保存内容，或读取空间人工关系图。");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const locked = busy || disabled;
  function edit(key: keyof typeof empty, value: string) { setFilters(old => ({ ...old, [key]: value })); setResult(null); setOpened(null); setPages([null]); }
  async function run(action: (controller: AbortController) => Promise<void>) {
    if (locked || pending.current) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setMessage("");
    try { await action(controller); }
    catch (error) { if (!controller.signal.aborted) { setResult(null); setGraph(null); setOpened(null); setMessage(`${error instanceof Error ? error.message : "读取失败"}；已清空旧结果，可手动重试。`); } }
    finally { if (!controller.signal.aborted) setBusy(false); if (pending.current === controller) pending.current = null; }
  }
  async function read(path: string, controller: AbortController) {
    const response = await fetch(`/api/account/spaces/${space}/${path}`, { cache: "no-store", signal: controller.signal, headers: { Authorization: `Bearer ${token}` } });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.code === "SEARCH_CURSOR_NOT_FOUND" ? "分页位置失效，请重新搜索" : value.error?.code ?? "读取失败");
    return value;
  }
  async function search(controller: AbortController, next = filters, cursors = [null] as (string | null)[]) {
    setResult(null); setOpened(null);
    const normalized = boundedFilters(next);
    if (!normalized.q || /[\u0000-\u001f\u007f]/.test(normalized.q)) throw new Error("请输入有效关键词");
    const query = new URLSearchParams({ limit: "20" });
    for (const [key, value] of Object.entries(normalized)) if (value) query.set(key, value);
    if (cursors.at(-1)) query.set("cursor", cursors.at(-1)!);
    const page = parseSearchPage(await read(`knowledge/search?${query}`, controller));
    if (controller.signal.aborted) return;
    setResult(page); setApplied(normalized); setPages(cursors);
    setMessage(`已读取第 ${cursors.length} 页 ${page.data.length} 条命中；结果绑定读取时的修订。`);
  }
  async function open(controller: AbortController, note: string, revisionId: string) {
    setOpened(null);
    const value = (await read(`notes/${note}/revisions/${revisionId}`, controller)).data;
    if (!value || value.id !== revisionId || !value.document || typeof value.content_md !== "string") throw new Error("修订响应无效");
    if (!controller.signal.aborted) { setOpened({ note, revision: value }); setMessage("已打开命中时的固定修订，可阅读结构化内容与加载原文证据。"); }
  }
  return <section className="knowledge-explorer" aria-label="知识组织与检索工作区">
    <h3>知识组织与检索</h3>
    <p>在最新修订的标题、正文、原文证据与批注中查找连续文字，可组合来源和分类。按笔记创建时间排序；每篇显示首个命中类别和最多240字片段，不计算语义相关度。历史修订的证据与批注不在搜索范围。</p>
    <form aria-label="知识内容检索" onSubmit={event => { event.preventDefault(); void run(controller => search(controller)); }}>
      <label>检索关键词<input aria-label="检索关键词" value={filters.q} disabled={locked} onChange={event => edit("q", event.target.value)} /></label>
      <label>检索内容范围<select aria-label="检索内容范围" value={filters.scope} disabled={locked} onChange={event => edit("scope", event.target.value)}><option value="all">全部内容</option>{Object.entries(searchFieldNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
      <label>检索来源类型<select aria-label="检索来源类型" value={filters.platform} disabled={locked} onChange={event => edit("platform", event.target.value)}><PlatformOptions /></select></label>
      <label>检索版本来源<select aria-label="检索版本来源" value={filters.origin} disabled={locked} onChange={event => edit("origin", event.target.value)}><option value="">全部版本来源</option><option value="ai_generated">模型生成</option><option value="user_edit">人工修订</option></select></label>
      <label>检索分类<select aria-label="检索分类" value={filters.label_id} disabled={locked} onChange={event => edit("label_id", event.target.value)}><option value="">全部分类</option>{labels.map(label => <option key={label.id} value={label.id}>{label.name}</option>)}</select></label>
      <p>分类选项来自已刷新的知识笔记目录，含归档分类；搜索不修改笔记。</p>
      <button className="button secondary" disabled={locked} type="submit">搜索知识内容</button>
    </form>
    <p role="status" aria-label="知识检索状态">{message}</p>
    {result && <>
      <p>本页条件：{applied.q} · {applied.scope === "all" ? "全部内容" : searchFieldNames[applied.scope as keyof typeof searchFieldNames]}。分页期间新版本可能变化，失效时请重新搜索。</p>
      <ContentPagination label="知识检索" page={pages.length} nextCursor={result.next_cursor} disabled={locked} onFirst={() => void run(controller => search(controller, applied))} onPrevious={() => void run(controller => search(controller, applied, pages.slice(0, -1)))} onNext={() => { if (result.next_cursor) void run(controller => search(controller, applied, [...pages, result.next_cursor])); }} />
      {result.data.length === 0 ? <p>当前条件没有命中已保存内容。</p> : <ul aria-label="知识检索结果">{result.data.map(hit => <li key={hit.note_id}>
        <h4>{hit.title} · 版本 {hit.version}</h4><p>命中：{searchFieldNames[hit.matched_field]}</p><blockquote className="reader-text">{hit.snippet}</blockquote>
        <button className="button secondary" disabled={locked} onClick={() => void run(controller => open(controller, hit.note_id, hit.revision_id))}>打开命中修订</button>
      </li>)}</ul>}
    </>}
    <SemanticSearch key={`semantic-${space}-${token}`} token={token} space={space} disabled={locked} />
    <section aria-label="空间人工关系图">
      <h4>空间人工关系图</h4><p>读取空间最近30篇有修订的笔记（含没有链接的笔记），仅显示所选节点之间最多60条有效人工链接。超限明确提示；没有画出的内容不代表不存在。位置、距离与线条不表示语义、支持或因果关系。</p>
      <button className="button secondary" disabled={locked} onClick={() => void run(async controller => { setGraph(null); setOpened(null); const value = parseSpaceGraph((await read("knowledge/graph", controller)).data); if (!controller.signal.aborted) { setGraph(value); setMessage(`已读取空间图：${value.nodes.length} 个节点、${value.edges.length} 条人工链接。`); } })}>读取空间人工关系图</button>
      {graph && <SpaceGraphView graph={graph} disabled={locked} onOpen={(note, revision) => void run(controller => open(controller, note, revision))} />}
    </section>
    {opened && <section aria-label="检索固定修订"><p>仅阅读命中时的修订，不随最新版本变化；修改与链接管理仍在下方知识笔记详情。</p><RevisionView key={`${opened.note}-${opened.revision.id}`} token={token} space={space} noteId={opened.note} revision={opened.revision} /></section>}
  </section>;
}
