"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useState } from "react";

type Paragraph = { kind: string; index: number; text: string };
type Annotation = { id: string; quote: string; body: string; kind: string; item_index: number; created_by: string; created_at: string };
const names: Record<string, string> = { overview: "概览", core_claims: "核心结论与观点", steps: "操作步骤与方法", examples: "案例与场景", numbers: "数字与条件", takeaways: "可复用启发" };

function paragraphs(document: Record<string, unknown>): Paragraph[] {
  const result: Paragraph[] = [];
  const overview = document.overview as { text?: unknown } | undefined;
  if (typeof overview?.text === "string") result.push({ kind: "overview", index: 0, text: overview.text });
  if (Array.isArray(document.sections)) for (const section of document.sections) {
    if (section && typeof section.kind === "string" && names[section.kind] && Array.isArray(section.items)) section.items.forEach((item: { text?: unknown }, index: number) => {
      if (typeof item?.text === "string") result.push({ kind: section.kind, index, text: item.text });
    });
  }
  return result;
}

export default function NoteAnnotations({ token, space, noteId, revision, disabled, onLockChange }: {
  token: string; space: string; noteId: string; revision: { id: string; version: number; document: Record<string, unknown> };
  disabled: boolean; onLockChange: (locked: boolean) => void;
}) {
  const field = useId();
  const options = paragraphs(revision.document);
  const [selected, setSelected] = useState("0");
  const [body, setBody] = useState("");
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<Annotation[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [message, setMessage] = useState("尚未加载当前版本批注。");
  const target = options[Number(selected)];
  const path = `/api/account/spaces/${space}/notes/${noteId}/revisions/${revision.id}/annotations`;
  useEffect(() => {
    onLockChange(busy || body.length > 0);
    return () => onLockChange(false);
  }, [busy, body, onLockChange]);

  async function load(cursor: string | null = null) {
    const response = await fetch(path + (cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""), { cache: "no-store", headers: { Authorization: `Bearer ${token}` } });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.code ?? "ANNOTATION_LOAD_FAILED");
    setRows(current => cursor ? [...(current ?? []), ...result.data] : result.data);
    setNext(result.next_cursor);
  }

  async function refresh(cursor: string | null = null) {
    setBusy(true);
    try { await load(cursor); setMessage("已加载当前修订的真实批注；其他版本的批注不会自动迁移。"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "批注加载失败"); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!target || busy || disabled) return;
    if (!body.trim() || body.length > 2000 || new TextEncoder().encode(body).length > 8000) {
      setMessage("批注需为 1–2000 字符且不超过 8000 字节 UTF-8。"); return;
    }
    setBusy(true); setAttempted(true);
    let saved = false;
    try {
      const response = await fetch(path, { method: "POST", cache: "no-store", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ idempotency_key: key, kind: target.kind, item_index: target.index, expected_quote: target.text, body }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "ANNOTATION_SAVE_FAILED");
      saved = true; setBody(""); setKey(crypto.randomUUID()); setAttempted(false);
      await load();
      setMessage("人工批注已保存到当前修订。笔记正文、证据与其他版本均保留。");
    } catch (error) {
      setMessage(saved ? "批注已保存，列表刷新失败，请手动刷新；无需再次提交。" : `${error instanceof Error ? error.message : "请求失败"}。重试使用原内容与幂等键；也可先刷新核对。`);
    } finally { setBusy(false); }
  }

  return <section aria-label="逐段人工批注">
    <h4>版本 {revision.version} 的人工批注</h4>
    <p>批注是未经来源或事实核验的个人观点；绑定本修订的原段引文，不自动迁移到新版本。当前只追加保存，暂不支持编辑或删除；Markdown 下载保留原笔记内容。</p>
    <label htmlFor={`${field}-anchor`}>批注段落</label><select id={`${field}-anchor`} value={selected} disabled={busy || disabled || attempted} onChange={event => setSelected(event.target.value)}>{options.map((item, index) => <option key={`${item.kind}-${item.index}`} value={index}>{names[item.kind]} · 第 {item.index + 1} 段</option>)}</select>
    {target && <blockquote aria-label="批注原段">{target.text}</blockquote>}
    <label htmlFor={`${field}-body`}>人工批注内容</label><textarea id={`${field}-body`} value={body} disabled={busy || disabled || attempted} onChange={event => setBody(event.target.value)} rows={4} />
    <p><button className="button primary" disabled={busy || disabled || !target || !body.trim()} onClick={() => void save()}>{attempted ? "以原键重试批注" : "保存人工批注"}</button>{" "}
      <button className="button secondary" disabled={busy || disabled || !body} onClick={() => { setBody(""); setAttempted(false); setKey(crypto.randomUUID()); setMessage("草稿已明确放弃；若此前提交结果未知，请刷新核对已保存批注。"); }}>放弃批注草稿</button>{" "}
      <button className="button secondary" disabled={busy || disabled} onClick={() => void refresh()}>刷新当前版本批注</button></p>
    <p role="status" aria-label="笔记批注状态">{message}</p>
    {rows && (rows.length === 0 ? <p>当前修订暂无批注。</p> : <ul aria-label="当前版本批注">{rows.map(row => <li key={row.id}>
      <p>{names[row.kind]} · 第 {row.item_index + 1} 段 · 作者 {row.created_by} · {row.created_at}</p>
      <blockquote>{row.quote}</blockquote><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{row.body}</pre>
    </li>)}</ul>)}
    {next && <button className="button secondary" disabled={busy || disabled} onClick={() => void refresh(next)}>加载更多当前版本批注</button>}
  </section>;
}
