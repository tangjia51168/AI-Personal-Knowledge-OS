"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import Link from "next/link";
import ReviewWorkbench from "./review-workbench";
import { useEffect, useId, useRef, useState } from "react";
import { readingStateNames } from "@/lib/learning-summary";
import { parseLearningSchedule, schedulePeriods, schedulePeriodNames, type SchedulePeriod, type LearningSchedule as Schedule } from "@/lib/learning-schedule";

type Props = { token: string; space: string };

function ScheduleResults({ token, space, period }: Props & { period: SchedulePeriod }) {
  const [data, setData] = useState<Schedule | null>(null);
  const [busy, setBusy] = useState(false);
  const [reviewNote, setReviewNote] = useState<string | null>(null);
  const [message, setMessage] = useState("尚未读取复习日程，请刷新。");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function read(download: boolean) {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true); setMessage(download ? "正在生成日历文件…" : "正在读取个人复习日程…");
    if (!download) { setData(null); setReviewNote(null); }
    try {
      const response = await fetch(`/api/account/spaces/${space}/learning/${download ? "calendar" : "schedule"}?period=${period}`, {
        cache: "no-store", signal: controller.signal, headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.error?.code ?? "LEARNING_SCHEDULE_FAILED");
      }
      if (download) {
        if (!response.headers.get("content-type")?.startsWith("text/calendar")) throw new Error("LEARNING_CALENDAR_INVALID");
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        if (blob.size > 262144) throw new Error("LEARNING_CALENDAR_LIMIT");
        const url = URL.createObjectURL(blob), link = document.createElement("a");
        link.href = url; link.download = "knowledge-review.ics";
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setMessage("已下载 ICS；内容以下载时重新读取为准。可手动导入日历；文件不会自动同步、取消旧事件或发送通知。");
      } else {
        const result = await response.json();
        const schedule = parseLearningSchedule(result.data, period);
        if (controller.signal.aborted) return;
        setData(schedule); setMessage(`已读取 ${schedule.items.length} 条个人复习日程${schedule.truncated ? "；超过100条，已明确截断，请使用更小范围或先处理逾期记录" : ""}。`);
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setData(null);
        setMessage(`${error instanceof Error ? error.message : "读取失败"}；未展示旧日程或生成新的下载文件，请手动刷新重试。`);
      }
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <div aria-busy={busy}>
    <p><button className="button secondary" disabled={busy || reviewNote !== null} onClick={() => void read(false)}>刷新复习日程</button>{" "}<button className="button secondary" disabled={busy || reviewNote !== null || !data || data.truncated || data.items.length === 0} onClick={() => void read(true)}>下载复习日历 ICS</button></p>
    {data && <>
      <p>读取时间：<time dateTime={data.as_of}>{new Date(data.as_of).toLocaleString()}</time>（本机时区）。{period !== "due" && <>范围截止：<time dateTime={data.until}>{new Date(data.until).toLocaleString()}</time>。</>}</p>
      {data.truncated && <p>仅显示最早的100条；为避免误以为导出完整范围，当前禁止日历下载。</p>}
      {data.items.length === 0 ? <p>所选范围暂无待复习提醒。</p> : <ol aria-label="个人复习日程条目">{data.items.map(item => <li key={item.note_id}>
        <p><time dateTime={item.reminder_at}>{new Date(item.reminder_at).toLocaleString()}</time> · {readingStateNames[item.status]}</p>
        <p>{item.title}</p><small>笔记 {item.note_id}</small>
        <p><button className="button secondary" disabled={busy || reviewNote !== null} onClick={() => setReviewNote(item.note_id)}>开始复习：{item.title}</button></p>
      </li>)}</ol>}
    </>}
    {reviewNote && <ReviewWorkbench key={reviewNote} token={token} space={space} noteId={reviewNote} onClose={() => setReviewNote(null)} onSaved={() => {setReviewNote(null);setData(null);setMessage("本次复习已保存。旧日程已清除，请刷新日程、个人学习总览和知识清单核对。");}} />}
    <p role="status" aria-label="复习日程状态">{message}</p>
  </div>;
}

export default function LearningSchedule({ token, space }: Props) {
  const id = useId();
  const [period, setPeriod] = useState<SchedulePeriod>("7");
  return <section className="learning-schedule" aria-label="个人复习日程与日历导出">
    <span className="progress-kicker">REVIEW AGENDA</span><h2>个人复习日程与日历导出</h2>
    <p>按已保存的个人提醒时间从早到晚排列，排除细看和没有提醒的笔记。未来范围从服务器读取时刻起算，按连续天数计算，不含已到期记录；时间按本机时区显示。</p>
    <label htmlFor={id}>复习日程范围</label>{" "}<select id={id} value={period} onChange={event => setPeriod(event.target.value as SchedulePeriod)}>{schedulePeriods.map(value => <option key={value} value={value}>{schedulePeriodNames[value]}</option>)}</select>
    <ScheduleResults key={`${space}-${token}-${period}`} token={token} space={space} period={period} />
    <p>ICS 是当前范围的静态导出，包含笔记标题与提醒时间；不含正文、账号凭证、外部链接或自动闹钟。重复导入和时区显示由日历客户端决定，第三方客户端尚未实测。</p>
    <p>修改状态或提醒后请手动刷新；可到<Link href="/account#knowledge">知识笔记</Link>按标题查找并调整。没有自动排课、间隔复习算法或通知发送。</p>
  </section>;
}
