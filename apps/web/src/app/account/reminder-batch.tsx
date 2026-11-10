"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { readingStateNames } from "@/lib/learning-summary";
import { reviewPlan } from "@/lib/review-plan";

export type ReminderNote = { id: string; title: string };
type Preview = { note_id: string; title: string; status: keyof typeof readingStateNames; reminder_at: string | null; expected: string };
type Props = { token: string; space: string; selection: ReminderNote[]; disabled: boolean; onClear: () => void; onChanged: () => void; onBusy: (busy: boolean) => void };

function validPreview(value: unknown, selection: ReminderNote[]): Preview[] {
  if (!Array.isArray(value) || value.length !== selection.length) throw new Error("REMINDER_PREVIEW_INVALID");
  return value.map((row, index) => {
    if (!row || row.note_id !== selection[index].id || typeof row.title !== "string" || !Object.hasOwn(readingStateNames, row.status)
      || typeof row.expected !== "string" || !/^[0-9a-f]{64}$/.test(row.expected)
      || (row.reminder_at !== null && (typeof row.reminder_at !== "string" || !Number.isFinite(Date.parse(row.reminder_at))))) throw new Error("REMINDER_PREVIEW_INVALID");
    return row as Preview;
  });
}

export default function ReminderBatch({ token, space, selection, disabled, onClear, onChanged, onBusy }: Props) {
  const [preview, setPreview] = useState<Preview[] | null>(null);
  const [when, setWhen] = useState("");
  const [clear, setClear] = useState(false);
  const [planned, setPlanned] = useState(false);
  const [perDay, setPerDay] = useState("3");
  const [interval, setInterval] = useState("1");
  const [confirmed, setConfirmed] = useState(false);
  const [message, setMessage] = useState("选择笔记后先读取预览，确认后整批设置或清除个人提醒。");
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  let planTimes: string[] | null = null;
  let planError = "";
  if (planned && preview) {
    try { planTimes = reviewPlan(preview.length, when, Number(perDay), Number(interval)); }
    catch (error) { planError = error instanceof Error ? error.message : "计划参数无效。"; }
  }

  async function act(save: boolean) {
    if (active.current || disabled || !selection.length || (save && (!preview || !confirmed || (planned ? !planTimes : !clear && !when)))) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); onBusy(true); setMessage(save ? "正在提交批量提醒…" : "正在读取所选笔记的最新个人记录…");
    try {
      const reminder = clear || planned ? null : when ? new Date(when).toISOString() : null;
      const items = save ? preview!.map((row, index) => ({ note_id: row.note_id, expected: row.expected, ...(planned ? { reminder_at: planTimes![index] } : {}) })) : undefined;
      const response = await fetch(`/api/account/spaces/${space}/learning/reminders/${save ? planned ? "plan" : "batch" : "preview"}`, {
        method: save ? "PUT" : "POST", cache: "no-store", signal: controller.signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(save ? planned ? { items } : { items, reminder_at: reminder } : { note_ids: selection.map(note => note.id) }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "REMINDER_BATCH_FAILED");
      if (controller.signal.aborted) return;
      if (save) {
        if (result.data?.selected !== selection.length || !Number.isInteger(result.data?.changed) || result.data.changed < 0 || result.data.changed > selection.length) throw new Error("REMINDER_RESULT_INVALID");
        setPreview(null); setConfirmed(false);
        setMessage(`${planned ? "复习计划已保存" : "批量提醒已提交"}：选择 ${result.data.selected} 篇，实际修改 ${result.data.changed} 篇。阅读状态保留；请刷新清单、学习总览与日程。`);
        onChanged();
      } else {
        setPreview(validPreview(result.data?.items, selection)); setConfirmed(false);
        setMessage("已读取最新预览。提交时若任一学习记录变化，整批拒绝；细看笔记的提醒不会进入待复习日程。");
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setPreview(null); setConfirmed(false);
        setMessage(`${error instanceof Error ? error.message : "请求失败"}；结果未确认，请重新读取预览核对后再提交，不会自动重试。`);
      }
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) { setBusy(false); onBusy(false); }
    }
  }

  return <section className="reminder-batch" aria-label="批量复习提醒安排" aria-busy={busy}>
    <h3>批量复习提醒安排</h3>
    <p>从笔记结果逐篇选择，最多20篇，跨页保留选择。只修改自己的提醒时间，保留阅读状态与完成时间；不会自动发送通知。</p>
    <p>已选择 {selection.length} / 20 篇。</p>
    {selection.length > 0 && <ul aria-label="批量提醒选择">{selection.map(note => <li key={note.id}>{note.title}</li>)}</ul>}
    <button className="button secondary" disabled={disabled || busy || !selection.length} onClick={() => void act(false)}>读取批量提醒预览</button>{" "}
    <button className="button secondary" disabled={disabled || busy || !selection.length} onClick={onClear}>清空提醒选择</button>
    {preview && <>
      <ul aria-label="批量提醒预览">{preview.map(row => <li key={row.note_id}>{row.title} · {readingStateNames[row.status]} · 原提醒 {row.reminder_at ? new Date(row.reminder_at).toLocaleString() : "未设置"}</li>)}</ul>
      <section aria-label="个人复习计划（按日分配）">
        <h4>个人复习计划（按日分配）</h4>
        <p><label><input type="checkbox" checked={planned} disabled={disabled || busy} onChange={event => { setPlanned(event.target.checked); setClear(false); setConfirmed(false); }} />按选择顺序分配复习日期</label></p>
        {planned && <>
          <p>按勾选顺序分组，每篇保存一次提醒；开始时间和每日时刻按本机时区，最多跨90个日历天。会覆盖原提醒，请逐篇核对；细看仍不列入待复习日程。</p>
          <p><label>每日篇数<input type="number" min="1" max="20" step="1" value={perDay} disabled={disabled || busy} onChange={event => { setPerDay(event.target.value); setConfirmed(false); }} /></label></p>
          <p><label>间隔天数<input type="number" min="1" max="30" step="1" value={interval} disabled={disabled || busy} onChange={event => { setInterval(event.target.value); setConfirmed(false); }} /></label></p>
          <p>跨夏令时尽量保持本机相同时刻，遇到不存在的时刻拒绝生成；重复时刻采用浏览器默认的较早时刻。不是重复复习算法、学习历史或后台自动排课。</p>
        </>}
      </section>
      {!planned && <p><label><input type="checkbox" checked={clear} disabled={disabled || busy} onChange={event => { setClear(event.target.checked); setConfirmed(false); }} />清除所选笔记的提醒时间</label></p>}
      {!clear && <p><label>{planned ? "计划开始时间" : "批量提醒时间"}<input type="datetime-local" value={when} disabled={disabled || busy} onChange={event => { setWhen(event.target.value); setConfirmed(false); }} /></label>（本机时区）</p>}
      {planned ? <>
        {planTimes && <ol aria-label="复习计划预览">{preview.map((row, index) => <li key={row.note_id}>{row.title} → {new Date(planTimes[index]).toLocaleString()}</li>)}</ol>}
        {planError && <p role="alert">{planError}</p>}
      </> : <p>本次操作：{clear ? "清除全部所选提醒" : when ? `统一设置为 ${new Date(when).toLocaleString()}` : "请填写提醒时间"}。</p>}
      <p><label><input type="checkbox" checked={confirmed} disabled={disabled || busy} onChange={event => setConfirmed(event.target.checked)} />确认修改以上所选笔记的个人提醒</label></p>
      <button className="button secondary" disabled={disabled || busy || !confirmed || (planned ? !planTimes : !clear && !when)} onClick={() => void act(true)}>{planned ? "保存复习计划" : "提交批量提醒"}</button>
    </>}
    <p role="status" aria-label="批量提醒状态">{message}</p>
  </section>;
}
