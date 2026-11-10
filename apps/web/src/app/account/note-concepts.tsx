"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useRef, useState } from "react";
import { conceptCards, type ConceptCard } from "@/lib/concept-cards";
import { readerDocument, readerEvidence, type ReaderEvidence } from "@/lib/note-reader";
import type { SavedRevision } from "./revision-view";

export default function NoteConcepts({ token, space, noteId, revision, disabled, onLockChange }: {
  token: string; space: string; noteId: string; revision: SavedRevision;
  disabled: boolean; onLockChange: (locked: boolean) => void;
}) {
  const field = useId();
  const pending = useRef<AbortController | null>(null);
  const submitted = useRef<object | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState("");
  const [selected, setSelected] = useState("");
  const [evidence, setEvidence] = useState<ReaderEvidence | null>(null);
  const [rows, setRows] = useState<ConceptCard[] | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [message, setMessage] = useState("尚未读取本修订概念与原文证据。");
  const path = `/api/account/spaces/${space}/notes/${noteId}/revisions/${revision.id}`;
  const quote = evidence?.quotes.find(row => row.key === selected);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    onLockChange(busy || !!name || !!definition || attempted);
    return () => onLockChange(false);
  }, [busy, name, definition, attempted, onLockChange]);

  async function request(suffix: string, controller: AbortController, body?: object) {
    const response = await fetch(path + suffix, {
      method: body ? "POST" : "GET", cache: "no-store", signal: controller.signal,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.code ?? "CONCEPT_REQUEST_FAILED");
    return result;
  }

  async function refresh() {
    if (pending.current || disabled) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setRows(null); setEvidence(null); if (!attempted) { setSelected(""); setConfirmed(false); }
    try {
      const result = await request("/concepts", controller);
      const cards = conceptCards(result.data, noteId, revision.id, revision.snapshot_id);
      const document = readerDocument(revision.document);
      if (!document) throw new Error("INVALID_READER_DOCUMENT");
      const keys = [...new Set([document.overview, ...document.sections.flatMap(s => s.items)].flatMap(item => item.evidence_keys))];
      const raw = await request("/export?format=json", controller);
      const loaded = readerEvidence(raw, noteId, revision.id, revision.snapshot_id, keys);
      if (!loaded) throw new Error("INVALID_READER_EVIDENCE");
      if (!controller.signal.aborted) {
        setRows(cards); setEvidence(loaded); if (!attempted) setSelected(loaded.quotes[0]?.key ?? "");
        setMessage(attempted ? "已重新读取，提交结果曾未知；请核对列表，原请求仍可用同键重试。" : "已读取本修订概念与原文证据。");
      }
    } catch (error) {
      if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "读取失败"}；旧列表与证据已清空，请手动重试。`);
    } finally {
      if (!controller.signal.aborted) { setBusy(false); pending.current = null; }
    }
  }

  async function save() {
    if (pending.current || disabled || (!quote && !submitted.current) || !confirmed || !name.trim() || !definition.trim()) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setAttempted(true);
    const body = submitted.current ?? { idempotency_key: key, name, definition, evidence_key: quote!.key, expected_quote: quote!.quote };
    submitted.current = body;
    try {
      const result = await request("/concepts", controller, body);
      conceptCards([result.data], noteId, revision.id, revision.snapshot_id);
      if (!controller.signal.aborted) {
        setRows(null); setEvidence(null); setSelected(""); setName(""); setDefinition("");
        setAttempted(false); setConfirmed(false); setKey(crypto.randomUUID()); submitted.current = null;
        setMessage("概念已保存。请重新读取核对；定义是人工解释，证据绑定不代表事实核验。");
      }
    } catch (error) {
      if (!controller.signal.aborted) { setRows(null); setEvidence(null); setMessage(`${error instanceof Error ? error.message : "请求失败"}。保存结果可能未知；保留原内容与幂等键，可同键重试或重新读取核对。`); }
    } finally {
      if (!controller.signal.aborted) { setBusy(false); pending.current = null; }
    }
  }

  const locked = busy || disabled || attempted;
  return <section className="note-concepts" aria-label="人工概念卡片">
    <h4>版本 {revision.version} 的人工概念卡片</h4>
    <p>名称与定义绑定本修订的一段原文证据；同名卡片可表达不同解释，不自动合并或迁移。当前仅追加，每修订最多20张、空间1000张；不支持修改/删除、全库概念目录或方向关系。</p>
    <button className="button secondary" disabled={busy || disabled} onClick={() => void refresh()}>读取本修订概念与证据</button>
    <label htmlFor={`${field}-name`}>概念名称</label><input id={`${field}-name`} value={name} maxLength={60} disabled={locked} onChange={event => { setName(event.target.value); setConfirmed(false); }} />
    <label htmlFor={`${field}-definition`}>人工概念定义</label><textarea id={`${field}-definition`} value={definition} maxLength={2000} rows={3} disabled={locked} onChange={event => { setDefinition(event.target.value); setConfirmed(false); }} />
    <label htmlFor={`${field}-evidence`}>概念原文证据</label><select id={`${field}-evidence`} value={selected} disabled={locked || !evidence} onChange={event => { setSelected(event.target.value); setConfirmed(false); }}>
      <option value="">请先读取证据</option>{evidence?.quotes.map(row => <option key={row.key} value={row.key}>{row.key}</option>)}
    </select>
    {quote && <blockquote aria-label="概念证据原文" className="reader-text">{quote.quote}</blockquote>}
    <label><input type="checkbox" checked={confirmed} disabled={locked || !quote} onChange={event => setConfirmed(event.target.checked)} />确认保存人工定义及本修订证据，未经事实核验</label>
    <p><button className="button primary" disabled={busy || disabled || !confirmed || (!quote && !attempted) || !name.trim() || !definition.trim()} onClick={() => void save()}>{attempted ? "同键重试概念保存" : "保存人工概念"}</button>{" "}
      <button className="button secondary" disabled={busy || disabled || (!name && !definition && !attempted)} onClick={() => { setName(""); setDefinition(""); setAttempted(false); setConfirmed(false); submitted.current = null; setKey(crypto.randomUUID()); setMessage("已放弃草稿。此前未知提交可能已保存，请重新读取核对。"); }}>放弃概念草稿</button></p>
    <p role="status" aria-label="人工概念状态">{message}</p>
    {rows && (rows.length ? <ul aria-label="本修订概念卡片">{rows.map(row => <li key={row.id}>
      <h5>{row.name}</h5><p className="reader-text">{row.definition}</p>
      <p>人工记录 · 置信度未评估 · 证据 {row.evidence_key} · 作者 {row.created_by} · {row.created_at}</p>
      <blockquote className="reader-text">{row.quote}</blockquote>
    </li>)}</ul> : <p>本修订暂无概念卡片。</p>)}
  </section>;
}
