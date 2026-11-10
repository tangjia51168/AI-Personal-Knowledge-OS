"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";
import { boundedFilters, contentPage, ContentPagination } from "./content-list";
import BatchExport from "./batch-export";
import RevisionView, { type SavedRevision } from "./revision-view";

type Note = { id: string; title: string; version: number };
type Detail = { id: string; revision: SavedRevision; revisions: Pick<SavedRevision, "id" | "title" | "version">[] };

export default function ExportCenter({ token, space }: { token: string; space: string }) {
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [pages, setPages] = useState<(string | null)[]>([null]);
  const [next, setNext] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [revision, setRevision] = useState<SavedRevision | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("请选择实际笔记与固定版本，再下载或加入批量清单。");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller; setBusy(true); setMessage("");
    try { await action(controller.signal); }
    catch (error) { if (!controller.signal.aborted) { setMessage(`${error instanceof Error ? error.message : "读取失败"}；请手动重试。`); setDetail(null); setRevision(null); } }
    finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }

  async function read(path: string, signal: AbortSignal) {
    const response = await fetch(`/api/account/spaces/${space}/${path}`, { cache: "no-store", signal, headers: { Authorization: `Bearer ${token}` } });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.code ?? "EXPORT_READ_FAILED");
    return value.data;
  }

  function load(q: string, cursors: (string | null)[] = [null]) {
    void run(async signal => {
      setNotes(null); setNext(null);
      const filters = boundedFilters({ q, platform: "" });
      const result = await contentPage<Note>(token, space, "notes", filters, cursors.at(-1) ?? null, signal);
      if (signal.aborted) return;
      setNotes(result.data); setNext(result.next_cursor); setApplied(filters.q); setPages(cursors);
      setMessage(`已加载第 ${cursors.length} 页，共 ${result.data.length} 篇真实笔记。`);
    });
  }

  return <section className="export-center" aria-label="笔记导出中心">
    <h2><Download size={21} /> 笔记导出中心</h2>
    <p>统一查看和下载固定修订：Markdown、HTML、JSON、纯文本、Obsidian ZIP、PPTX、图文 SVG 和流程 SVG。仅导出所选笔记与证据，不是完整空间备份。</p>
    <div className="export-studio"><div className="export-library"><span className="account-eyebrow">LIBRARY · 选择内容</span><form onSubmit={event => { event.preventDefault(); load(query); }}><label>导出笔记关键词<input value={query} maxLength={120} disabled={busy} onChange={event => setQuery(event.target.value)} /></label><button className="button secondary" disabled={busy}>查找可导出笔记</button></form>
    {notes && <ContentPagination label="导出笔记" page={pages.length} nextCursor={next} disabled={busy} onFirst={() => load(applied)} onPrevious={() => load(applied, pages.slice(0, -1))} onNext={() => { if (next) load(applied, [...pages, next]); }} />}
    {notes?.length === 0 && <p>暂无匹配笔记；先在收件箱导入正文并创建人工证据笔记，或授权生成。</p>}
    <ul>{notes?.map(note => <li key={note.id}><span>{note.title} · 列表版本 {note.version}</span><button className="button secondary" disabled={busy} onClick={() => void run(async signal => {
      setDetail(null); setRevision(null);
      const result = await read(`notes/${note.id}`, signal) as Detail;
      if (!signal.aborted) { setDetail(result); setRevision(result.revision); setMessage("已固定当前修订；刷新列表不会自动更换此修订。"); }
    })}>选择导出版本</button></li>)}</ul>
    {detail && revision && <label>导出修订<select aria-label="导出修订" value={revision.id} disabled={busy} onChange={event => {
      const selected = event.target.value;
      void run(async signal => { setRevision(null); const result = await read(`notes/${detail.id}/revisions/${selected}`, signal) as SavedRevision; if (!signal.aborted) setRevision(result); });
    }}>{detail.revisions.map(item => <option key={item.id} value={item.id}>版本 {item.version} · {item.title}</option>)}</select></label>}
    <BatchExport token={token} space={space} current={detail && revision ? { note_id: detail.id, revision_id: revision.id, title: revision.title } : null} />
    </div><div className="export-preview"><span className="account-eyebrow">PREVIEW · 固定版本</span>{detail && revision ? <RevisionView token={token} space={space} noteId={detail.id} revision={revision} /> : <div className="studio-empty"><Download size={38} aria-hidden="true" /><h3>选择一篇笔记，预览导出内容</h3><p>先搜索并固定修订，再选择格式下载。PPTX 与 SVG 的实际效果以下载文件为准。</p></div>}</div></div>
    <p role="status" aria-label="导出中心状态">{message}</p>
  </section>;
}
