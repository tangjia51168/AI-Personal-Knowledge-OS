"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";

type Row = { id: string; note_id: string; status: string; reminder_at: string | null; completed_at: string | null };
const names: Record<string, string> = { unseen: "未看", seen: "已看", skimmed: "粗看", deep_read: "细看" };

function localTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export default function Learning({ token, space, noteId, onSaved }: { token: string; space: string; noteId?: string; onSaved?: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [status, setStatus] = useState("unseen");
  const [reminder, setReminder] = useState("");
  const [message, setMessage] = useState("尚未加载学习状态，请刷新。");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); }, []);

  function applyRow(row?: Row) {
    setStatus(row?.status ?? "unseen");
    setReminder(localTime(row?.reminder_at ?? null));
  }

  async function load() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true); setLoaded(false); setRows([]);
    try {
      const query = noteId ? "?note_id=" + encodeURIComponent(noteId) : "?due=true";
      const response = await fetch(`/api/account/spaces/${space}/learning${query}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "LEARNING_LOAD_FAILED");
      if (controller.signal.aborted) return;
      setRows(result.data);
      if (noteId) applyRow(result.data.find((row: Row) => row.note_id === noteId));
      setLoaded(true);
      setMessage(noteId ? "已读取本笔记的个人学习记录；没有记录时默认为未看。" : `已读取 ${result.data.length} 条到期记录（最多100条）。到期不代表已发送邮件或推送。`);
    } catch (error) {
      if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "加载失败"}；未展示旧提醒，请手动重试。`);
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function save() {
    if (!noteId || !loaded || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    try {
      const response = await fetch(`/api/account/spaces/${space}/notes/${noteId}/learning`, { method: "PUT", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ status, reminder_at: reminder ? new Date(reminder).toISOString() : null }), cache: "no-store", signal: controller.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "LEARNING_SAVE_FAILED");
      if (controller.signal.aborted) return;
      applyRow(result.data);
      onSaved?.();
      setMessage(`已保存学习状态：${names[result.data.status]}。提醒时间仅作为到期记录，不代表已发送通知。`);
    } catch (error) {
      if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "保存失败"}；结果未确认，可先刷新核对再重试。`);
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section aria-label={noteId ? "笔记学习状态" : "到期学习提醒"}>
    <h4>{noteId ? "学习状态与提醒" : "到期学习提醒"}</h4>
    <p>每位成员独立记录；提醒时间按本机时区填写。细看不再列入到期记录；不会自动发送邮件或推送。</p>
    {noteId ? <>
      <label>阅读状态<select value={status} disabled={busy || !loaded} onChange={event => setStatus(event.target.value)}>{Object.entries(names).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{" "}
      <label>提醒时间<input type="datetime-local" value={reminder} disabled={busy || !loaded} onChange={event => setReminder(event.target.value)} /></label>{" "}
      <button className="button secondary" disabled={busy || !loaded} onClick={() => void save()}>保存学习状态</button>
    </> : loaded && (rows.length ? <ul>{rows.map(row => <li key={row.id}>笔记 {row.note_id} · {names[row.status]} · 到期 {row.reminder_at && new Date(row.reminder_at).toLocaleString()}</li>)}</ul> : <p>暂无到期提醒。</p>)}
    <button className="button secondary" disabled={busy} onClick={() => void load()}>刷新学习状态</button>
    <p role="status" aria-label="学习状态">{message}</p>
  </section>;
}
