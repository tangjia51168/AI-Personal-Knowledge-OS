"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { BookOpen, CalendarClock, CheckCircle2 } from "lucide-react";
import { parseLearningSummary, readingStates, readingStateNames, type LearningSummary as Summary } from "@/lib/learning-summary";

export default function LearningSummary({ token, space }: { token: string; space: string }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [message, setMessage] = useState("尚未加载个人学习进度，请刷新。");
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function refresh() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true); setSummary(null); setMessage("正在读取个人学习记录…");
    try {
      const response = await fetch(`/api/account/spaces/${space}/learning/summary`, {
        cache: "no-store", signal: controller.signal, headers: { Authorization: `Bearer ${token}` },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "LEARNING_SUMMARY_FAILED");
      const data = parseLearningSummary(result.data);
      if (controller.signal.aborted) return;
      setSummary(data); setMessage("已读取当前空间的个人学习状态计数；状态由自己标记，不代表知识掌握程度。");
    } catch (error) {
      if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "读取失败"}；未展示旧进度，请手动重试。`);
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="learning-overview" aria-label="个人学习进度总览" aria-busy={busy}>
    <span className="progress-kicker">PERSONAL LEARNING</span>
    <h2><BookOpen size={22} aria-hidden="true" /> 个人学习进度总览</h2>
    <p>只统计当前空间中你自己的阅读状态，按笔记而非修订计数；未保存学习记录的笔记计入“未看”。不推断掌握率，不显示其他成员的记录。</p>
    <button className="button secondary" disabled={busy} onClick={() => void refresh()}>刷新个人学习进度</button>
    {summary && <>
      <p>统计时间：<time dateTime={summary.as_of}>{new Date(summary.as_of).toLocaleString()}</time>（本机时区）</p>
      <dl className="learning-counts" aria-label="阅读状态统计">
        <div><dt><BookOpen size={16} aria-hidden="true" /> 笔记总数</dt><dd>{summary.total_notes}</dd></div>
        <div><dt>已保存个人学习记录</dt><dd>{summary.recorded_notes}</dd></div>
        <div><dt>尚无个人学习记录</dt><dd>{summary.unrecorded_notes}</dd></div>
        {readingStates.map(state => <div key={state}><dt><CheckCircle2 size={16} aria-hidden="true" /> {readingStateNames[state]}</dt><dd>{summary.states[state]}</dd><progress aria-label={`${readingStateNames[state]}笔记占比`} max={summary.total_notes || 1} value={summary.states[state]} /></div>)}
        <div><dt><CalendarClock size={16} aria-hidden="true" /> 已到期提醒</dt><dd>{summary.reminders.due}</dd></div>
        <div><dt>未来提醒</dt><dd>{summary.reminders.upcoming}</dd></div>
      </dl>
      <p>已到期与未来提醒均排除“细看”，只统计已保存的提醒时间；不代表通知已发送。统计覆盖全部笔记，不受到期列表最多100条限制。新修订不会自动重置整篇笔记的阅读状态。</p>
      {summary.total_notes === 0 && <p>当前空间还没有笔记；不会自动创建演示数据。</p>}
    </>}
    <p role="status" aria-label="个人学习进度状态">{message}</p>
  </section>;
}
