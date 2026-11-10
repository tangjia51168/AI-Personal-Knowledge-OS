"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { Search, BookOpen } from "lucide-react";
import { parseSearchPage, type SearchPage } from "@/lib/knowledge-explorer";
import RevisionView, { type SavedRevision } from "./revision-view";

type Research = { question: string; mode: "evidence_retrieval"; answer: null; answer_status: "evidence_ready" | "insufficient_evidence"; sources: SearchPage["data"]; next_cursor: string | null; limitations: string[] };

export default function Research({ token, space }: { token: string; space: string }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<Research | null>(null);
  const [opened, setOpened] = useState<{ note: string; revision: SavedRevision } | null>(null);
  const [message, setMessage] = useState("输入资料中的关键词，先定位证据，再阅读原文。不会自动调用模型。");
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function read(path: string, signal: AbortSignal) {
    const response = await fetch(`/api/account/spaces/${space}/${path}`, { cache: "no-store", signal, headers: { Authorization: `Bearer ${token}` } });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.code ?? "RESEARCH_UNAVAILABLE");
    return value;
  }

  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setMessage("");
    try { await action(controller.signal); }
    catch (error) { if (!controller.signal.aborted) { setResult(null); setOpened(null); setMessage(`${error instanceof Error ? error.message : "读取失败"}；已清空旧结果，可重试。`); } }
    finally { if (active.current === controller) active.current = null; if (!controller.signal.aborted) setBusy(false); }
  }

  async function search(signal: AbortSignal) {
    setResult(null); setOpened(null);
    const question = query.trim();
    if (!question || question.length > 120 || /[\u0000-\u001f\u007f]/.test(question)) throw new Error("请输入1–120字符的有效关键词");
    const value = (await read(`knowledge/research?${new URLSearchParams({ q: question, limit: "10" })}`, signal)).data;
    const page = parseSearchPage({ data: value?.sources, next_cursor: value?.next_cursor });
    if (!value || value.question !== question || value.mode !== "evidence_retrieval" || value.answer !== null || !["evidence_ready", "insufficient_evidence"].includes(value.answer_status) || !Array.isArray(value.limitations) || !value.limitations.every((text: unknown) => typeof text === "string")) throw new Error("研究响应无效");
    if (!signal.aborted) { setResult({ ...value, sources: page.data }); setMessage(page.data.length ? `找到 ${page.data.length} 条证据候选。尚未进行模型综合，请逐项核对。` : "资料不足：当前关键词未匹配到已保存内容。请调整关键词或先导入素材。"); }
  }

  return <section className="research-workbench" aria-label="证据研究工作台">
    <div className="research-title"><Search size={21} /><div><p className="account-eyebrow">EVIDENCE FIRST</p><h2>证据研究</h2></div><span className="progress-badge">无模型调用</span></div>
    <p>将问题拆成资料中的关键词，定位准确修订并核对来源。这里仅做无模型检索；下方基础问答需单独授权。语义检索和自动共识/冲突判定尚未实现。</p>
    <form onSubmit={event => { event.preventDefault(); void run(search); }}><label>研究关键词<input value={query} maxLength={120} disabled={busy} placeholder="例如：准备材料、RAG、学习计划" onChange={event => { setQuery(event.target.value); setResult(null); setOpened(null); }} /></label><button className="button primary" disabled={busy || !query.trim()} type="submit"><Search size={15} />查找研究证据</button></form>
    <p role="status" aria-label="证据研究状态">{message}</p>
    {result && <><ul className="research-results">{result.sources.map((hit, index) => <li key={hit.note_id}><span className="research-number">{String(index + 1).padStart(2, "0")}</span><div><h3>{hit.title}</h3><small>版本 {hit.version} · 固定修订 {hit.revision_id}</small><blockquote className="reader-text">{hit.snippet}</blockquote><button className="button secondary" disabled={busy} onClick={() => void run(async signal => { setOpened(null); const revision = (await read(`notes/${hit.note_id}/revisions/${hit.revision_id}`, signal)).data; if (!revision || revision.id !== hit.revision_id || typeof revision.content_md !== "string" || !revision.document) throw new Error("修订响应无效"); if (!signal.aborted) setOpened({ note: hit.note_id, revision }); })}><BookOpen size={14} />阅读此证据修订</button></div></li>)}</ul>{result.next_cursor && <p>本次仅显示前10条候选；请在知识组织与检索中继续分页或缩小关键词。</p>}<details><summary>研究范围与限制</summary><ul>{result.limitations.map(text => <li key={text}>{text}</li>)}</ul></details></>}
    {opened && <RevisionView key={opened.revision.id} token={token} space={space} noteId={opened.note} revision={opened.revision} />}
  </section>;
}
