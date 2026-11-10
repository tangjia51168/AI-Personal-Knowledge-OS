"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import RevisionView, { type SavedRevision } from "./revision-view";
import { parseResearchRun, type Citation, type ResearchRun } from "@/lib/research-answer";

export default function ResearchAnswer({ token, space, canGenerate }: { token: string; space: string; canGenerate: boolean }) {
  const [question, setQuestion] = useState("");
  const [query, setQuery] = useState("");
  const [consent, setConsent] = useState(false);
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [history, setHistory] = useState<ResearchRun[]>([]);
  const [opened, setOpened] = useState<{ note: string; revision: SavedRevision } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const active = useRef<AbortController | null>(null);
  const key = useRef(crypto.randomUUID());
  const [submitted, setSubmitted] = useState(false);
  useEffect(() => () => active.current?.abort(), []);
  const locked = submitted || run !== null;

  async function loadHistory() {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setMessage(""); setHistory([]); setOpened(null);
    try {
      const response = await fetch(`/api/account/spaces/${space}/knowledge/answers`, { signal: controller.signal, cache: "no-store", headers: { Authorization: `Bearer ${token}` } });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error?.code ?? "RESEARCH_FAILED");
      if (!Array.isArray(value.data) || value.data.length > 20) throw new Error("INVALID_RESEARCH_RESPONSE");
      const rows = value.data.map((row: unknown) => parseResearchRun(row));
      if (new Set(rows.map((row: ResearchRun) => row.id)).size !== rows.length) throw new Error("INVALID_RESEARCH_RESPONSE");
      if (!controller.signal.aborted) { setHistory(rows); setMessage(`已读取最近 ${rows.length} 条个人问答记录；读取不会调用模型。`); }
    } catch (error) { if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "读取失败"}。旧历史已清空，可重新读取。`); }
    finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }

  async function act(refresh = false, citation?: Citation) {
    if (active.current || (!citation && !refresh && !consent)) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setMessage(""); setOpened(null);
    try {
      if (!refresh && !citation) setSubmitted(true);
      const path = citation ? `notes/${citation.note_id}/revisions/${citation.revision_id}` : refresh && run ? `knowledge/answers/${run.id}` : "knowledge/answers";
      const response = await fetch(`/api/account/spaces/${space}/${path}`, {
        method: citation || (refresh && run) ? "GET" : "POST", cache: "no-store", signal: controller.signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        ...(!citation && !(refresh && run) ? { body: JSON.stringify({ idempotency_key: key.current, consent: true, question, query }) } : {}),
      });
      const result = await response.json();
      if (!response.ok) {
        // A rejected validation/no-provider request is safe to edit; network/5xx
        // uncertainty remains locked to its original key for explicit recovery.
        if ([400, 401, 403, 404, 422, 429].includes(response.status) && !run) setSubmitted(false);
        throw new Error(result.error?.code ?? "RESEARCH_FAILED");
      }
      if (controller.signal.aborted) return;
      if (citation) {
        if (result.data?.id !== citation.revision_id || result.data?.snapshot_id !== citation.snapshot_id) throw new Error("RESEARCH_REVISION_MISMATCH");
        setOpened({ note: citation.note_id, revision: result.data });
      } else {
        const parsed = parseResearchRun(result.data, run ? { id: run.id } : { question, query });
        setRun(parsed);
        setMessage(parsed.status === "succeeded" ? "已保存本次研究结果；请逐条核对原文引用。" : `研究状态：${parsed.status} ${parsed.error_code ?? ""}。未确认的请求不会自动重复调用。`);
      }
    } catch (error) { if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "请求失败"}。请查询/按原键核对，不自动追加付费请求。`); }
    finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }

  return <section className="research-answer" aria-label="知识库证据问答">
    <h3>知识库证据问答 · 基础 RAG</h3>
    <p>按检索关键词匹配最新笔记，最多取8段真实引文后生成回答。不是向量/语义检索，也不保证事实正确。仅支持已授权 DeepSeek；没有证据时直接报告不足，不调用模型。</p>
    <button className="button secondary" disabled={busy} onClick={() => void loadHistory()}>读取个人问答记录</button>
    <ul>{history.map(row => <li key={row.id}>{row.question} · {row.status} <button className="button secondary" disabled={busy || submitted && !run} onClick={() => { setQuestion(row.question); setQuery(row.query); setRun(row); setSubmitted(true); setConsent(false); setOpened(null); setMessage("已打开已保存记录，可查询状态或核对固定引文。"); }}>打开问答记录</button></li>)}</ul>
    {!canGenerate ? <p>成员可使用上方无模型证据检索；发送空间证据到供应商与费用授权需要管理员。</p> : <>
      <label>研究问题<textarea value={question} maxLength={1000} disabled={busy || locked} onChange={event => { setQuestion(event.target.value); setConsent(false); key.current = crypto.randomUUID(); }} /></label>
      <label>问答检索关键词<input value={query} maxLength={120} disabled={busy || locked} onChange={event => { setQuery(event.target.value); setConsent(false); key.current = crypto.randomUUID(); }} /></label>
      <label><input type="checkbox" checked={consent} disabled={busy || locked} onChange={event => setConsent(event.target.checked)} />我同意将问题和检索到的证据发送至已授权供应商，并承担本次费用</label>
      <button className="button primary" disabled={busy || locked || !consent || !question.trim() || !query.trim()} onClick={() => void act()}>授权并生成证据回答</button>{" "}
      {locked && <button className="button secondary" disabled={busy} onClick={() => void act(true)}>{run ? "查询研究状态" : "按原键核对本次请求"}</button>}
      {run && ["succeeded", "failed"].includes(run.status) && <button className="button secondary" disabled={busy} onClick={() => { setSubmitted(false); key.current = crypto.randomUUID(); setRun(null); setConsent(false); setOpened(null); setMessage("新请求需要重新授权；旧结果保留在服务器，不自动重试。"); }}>准备新的研究问题</button>}
    </>}
    <p role="status" aria-label="证据问答状态">{message}</p>
    {run?.answer && <div><h4>{run.answer.insufficient_evidence ? "资料不足／仅能回答部分内容" : "基于检索证据的回答"}</h4><ol>{run.answer.statements.map((item, index) => <li key={index}>{item.text} <small>{item.citations.join(" · ")}</small></li>)}</ol><ul>{run.answer.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
    {run?.citations.map(item => <details key={item.id}><summary>{item.id} · {item.title} · {item.evidence_key}</summary><blockquote>{item.quote}</blockquote><button className="button secondary" disabled={busy} onClick={() => void act(false, item)}>核对 {item.id} 原文版本</button></details>)}
    {opened && <RevisionView token={token} space={space} noteId={opened.note} revision={opened.revision} />}
  </section>;
}
