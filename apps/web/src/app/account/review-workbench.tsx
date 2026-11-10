"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { readingStateNames } from "@/lib/learning-summary";
import NoteReader from "./note-reader";
import EvidenceRecall from "./evidence-recall";

type Revision = { id: string; snapshot_id: string; origin: string; title: string; content_md: string; document: Record<string, unknown> };
type Preview = { note_id: string; title: string; status: keyof typeof readingStateNames; reminder_at: string | null; expected: string };
type Props = { token: string; space: string; noteId: string; onClose: () => void; onSaved: () => void };

export default function ReviewWorkbench({ token, space, noteId, onClose, onSaved }: Props) {
  const [data, setData] = useState<{ revision: Revision; preview: Preview } | null>(null);
  const [status, setStatus] = useState("seen");
  const [next, setNext] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("先读取本笔记与最新个人记录，再阅读并确认本次复习。");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function act(save: boolean) {
    if (active.current || (save && (!data || !confirmed))) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setMessage(save ? "正在保存本次复习…" : "正在读取笔记和最新个人记录…");
    if (!save) { setData(null); setConfirmed(false); }
    const root = `/api/account/spaces/${space}`;
    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    async function request(path: string, init?: RequestInit) {
      const response = await fetch(root + path, { ...init, headers, cache: "no-store", signal: controller.signal });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error?.code ?? "REVIEW_FAILED");
      return value.data;
    }
    try {
      if (save) {
        const result = await request(`/notes/${noteId}/review`, { method: "PUT", body: JSON.stringify({ status, reminder_at: next ? new Date(next).toISOString() : null, expected: data!.preview.expected }) });
        if (controller.signal.aborted) return;
        if (result.note_id !== noteId || result.status !== status) throw new Error("REVIEW_RESULT_INVALID");
        onSaved();
      } else {
        const [note, selection] = await Promise.all([
          request(`/notes/${noteId}`),
          request("/learning/reminders/preview", {method:"POST",body:JSON.stringify({note_ids:[noteId]})}),
        ]);
        if (controller.signal.aborted) return;
        const preview = selection.items?.[0], revision = note.revision;
        if (note.id !== noteId || !revision || typeof revision.id !== "string" || typeof revision.snapshot_id !== "string"
          || typeof revision.title !== "string" || typeof revision.content_md !== "string" || !revision.document
          || !["ai_generated","user_edit"].includes(revision.origin) || selection.items.length !== 1 || preview.note_id !== noteId
          || typeof preview.expected !== "string" || !/^[0-9a-f]{64}$/.test(preview.expected) || !Object.hasOwn(readingStateNames,preview.status)) throw new Error("REVIEW_PREVIEW_INVALID");
        setData({revision,preview}); setStatus(preview.status === "unseen" ? "seen" : preview.status); setNext("");
        setMessage("已读取笔记与个人记录。选择本次阅读状态；留空下一次提醒会清除原提醒，请核对后确认。");
      }
    } catch (error) {
      if (!controller.signal.aborted) { setData(null); setConfirmed(false); setMessage(`${error instanceof Error ? error.message : "请求失败"}；结果未确认，请重新读取核对，不会自动重试。`); }
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="review-workbench" aria-label="个人复习工作台" aria-busy={busy}>
    <h3>个人复习工作台</h3>
    <p>阅读后由你确认状态，不推断掌握率。保存本次完成时间，并清除或改为下一次提醒；没有复习次数历史或自动间隔算法。</p>
    <button className="button secondary" disabled={busy} onClick={() => void act(false)}>读取复习笔记与个人记录</button>{" "}
    <button className="button secondary" disabled={busy} onClick={onClose}>关闭复习工作台</button>
    {data && <>
      <h4>{data.revision.title}</h4><p>当前个人状态：{readingStateNames[data.preview.status]}；原提醒：{data.preview.reminder_at ? new Date(data.preview.reminder_at).toLocaleString() : "未设置"}。</p>
      <p>阅读修订：{data.revision.id}。状态关联整篇笔记，新修订不会使本次确认自动失效；需要时重新读取。</p>
      <NoteReader key={data.revision.id} token={token} space={space} noteId={noteId} revision={data.revision} />
      <EvidenceRecall key={`recall-${data.revision.id}`} space={space} noteId={noteId} revision={data.revision} />
      <details><summary>查看复习原始 Markdown</summary><pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{data.revision.content_md}</pre></details>
      <p><label>本次阅读状态<select aria-label="本次阅读状态" value={status} disabled={busy} onChange={event => {setStatus(event.target.value);setConfirmed(false);}}>{(["seen","skimmed","deep_read"] as const).map(value => <option key={value} value={value}>{readingStateNames[value]}</option>)}</select></label></p>
      <p><label>下一次复习提醒<input type="datetime-local" value={next} disabled={busy} onChange={event => {setNext(event.target.value);setConfirmed(false);}} /></label>（本机时区；留空清除原提醒）</p>
      <p>本次保存：{readingStateNames[status as keyof typeof readingStateNames]}；{next ? `下次提醒 ${new Date(next).toLocaleString()}` : "清除原提醒"}。细看仍排除待复习日程，提醒不代表通知。</p>
      <p><label><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} />确认已阅读并保存以上状态与提醒</label></p>
      <button className="button secondary" disabled={busy || !confirmed} onClick={() => void act(true)}>保存本次复习</button>
    </>}
    <p role="status" aria-label="复习工作台状态">{message}</p>
  </section>;
}
