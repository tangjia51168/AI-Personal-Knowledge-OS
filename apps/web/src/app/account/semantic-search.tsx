"use client";

import { useEffect, useRef, useState } from "react";
import { accountFetch as fetch } from "@/lib/account-fetch";
import RevisionView, { type SavedRevision } from "./revision-view";

const model = "multilingual-minilm-l12-v2-quint8-avx2-e8f8c211-mean128-v1";
type Hit = { note_id: string; revision_id: string; title: string; version: number; snippet: string; similarity: number; position: number };
type Result = { model: string; coverage: { latest_notes: number; indexed_latest_notes: number; long_notes: number }; data: Hit[] };
type Props = { token: string; space: string; disabled: boolean };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export default function SemanticSearch({ token, space, disabled }: Props) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [opened, setOpened] = useState<{ note: string; revision: SavedRevision } | null>(null);
  const [busy, setBusy] = useState(false), [status, setStatus] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (disabled || pending.current) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setStatus(""); setOpened(null);
    try { await action(controller.signal); }
    catch (error) {
      if (!controller.signal.aborted) { setResult(null); setOpened(null); setStatus(`${error instanceof Error ? error.message : "请求失败"}；已清空旧结果，可手动重试。`); }
    } finally { if (!controller.signal.aborted) setBusy(false); if (pending.current === controller) pending.current = null; }
  }
  async function read(path: string, signal: AbortSignal) {
    const response = await fetch(`/api/account/spaces/${space}/${path}`, { signal, cache: "no-store", headers: { Authorization: `Bearer ${token}` } });
    const value = await response.json();
    if (!response.ok) throw new Error(["LOCAL_EMBEDDING_NOT_CONFIGURED", "LOCAL_EMBEDDING_UNAVAILABLE"].includes(value.error?.code) ? "本地向量模型尚未启用或暂不可用" : value.error?.code ?? "读取失败");
    return value.data;
  }
  return <section aria-label="本地语义检索" className="semantic-search">
    <h4>用含义寻找笔记</h4>
    <p>多语言本地模型寻找近义内容，按余弦相似度排序。仅搜索已建立索引的最新修订，分数不是正确率或事实核验；不会调用付费模型。</p>
    <form onSubmit={event => { event.preventDefault(); void run(async signal => {
      setResult(null);
      if (!query.trim() || query.trim().length > 96) throw new Error("请输入1至96字的查询");
      const value = await read(`knowledge/semantic/search?${new URLSearchParams({ q: query.trim(), limit: "10" })}`, signal) as Result;
      if (value?.model !== model || !Array.isArray(value.data) || value.data.length > 10 || !value.coverage || ![value.coverage.latest_notes, value.coverage.indexed_latest_notes, value.coverage.long_notes].every(n => Number.isInteger(n) && n >= 0) || value.coverage.indexed_latest_notes > value.coverage.latest_notes || value.data.some(hit => !uuid.test(hit.note_id) || !uuid.test(hit.revision_id) || typeof hit.title !== "string" || !Number.isInteger(hit.version) || hit.version < 1 || typeof hit.snippet !== "string" || hit.snippet.length > 192 || !Number.isFinite(hit.similarity) || hit.similarity < 0.35 || hit.similarity > 1.00001)) throw new Error("语义检索响应无效");
      if (!signal.aborted) { setResult(value); setStatus(`找到 ${value.data.length} 篇候选笔记，请打开修订核对证据。`); }
    }); }}>
      <label>语义查询<input aria-label="语义查询" maxLength={96} value={query} disabled={busy || disabled} onChange={event => { setQuery(event.target.value); setResult(null); setOpened(null); }} /></label>
      <button type="submit" className="button secondary" disabled={busy || disabled}>按含义检索</button>
    </form>
    <p role="status" aria-label="语义检索状态">{status}</p>
    {result && <>
      <p>最新修订索引覆盖：{result.coverage.indexed_latest_notes} / {result.coverage.latest_notes} 篇；{result.coverage.long_notes} 篇超过单篇12288字上限。未索引、新版本和上限外内容不在结果内。</p>
      {result.data.length === 0 ? <p>当前索引中没有达到相似度阈值的候选；可调整表达或使用字面检索。</p> : <ul aria-label="语义检索结果">{result.data.map(hit => <li key={hit.note_id}>
        <h4>{hit.title} · 版本 {hit.version}</h4>
        <p>余弦相似度 {hit.similarity.toFixed(3)} · 索引文本段 {hit.position + 1}</p>
        <blockquote className="reader-text">{hit.snippet}</blockquote>
        <button className="button secondary" disabled={busy || disabled} onClick={() => void run(async signal => {
          const revision = await read(`notes/${hit.note_id}/revisions/${hit.revision_id}`, signal) as SavedRevision;
          if (!revision || revision.id !== hit.revision_id || !revision.document || typeof revision.content_md !== "string") throw new Error("修订响应无效");
          if (!signal.aborted) { setOpened({ note: hit.note_id, revision }); setStatus("已打开匹配的固定修订，可继续核对原文证据。"); }
        })}>打开语义命中修订</button>
      </li>)}</ul>}
    </>}
    {opened && <RevisionView key={opened.revision.id} token={token} space={space} noteId={opened.note} revision={opened.revision} />}
    <p>管理员可在笔记详情中，为明确选定的修订建立本地索引。模型最长128个token；每段最多96字仍可能截断，不能据此声称全文语义覆盖。</p>
  </section>;
}

