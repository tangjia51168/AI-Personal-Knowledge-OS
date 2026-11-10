"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { parseLearningAnalysis, readingStates, readingStateNames, type LearningAnalysis as Analysis } from "@/lib/learning-analysis";

export default function LearningAnalysis({ token, space }: { token: string; space: string }) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [message, setMessage] = useState("尚未加载个人复习分析，请刷新。");
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function refresh() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true); setAnalysis(null); setMessage("正在读取个人复习分析…");
    try {
      const response = await fetch(`/api/account/spaces/${space}/learning/analysis`, { cache: "no-store", signal: controller.signal, headers: { Authorization: `Bearer ${token}` } });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "LEARNING_ANALYSIS_FAILED");
      const data = parseLearningAnalysis(result.data);
      if (controller.signal.aborted) return;
      setAnalysis(data); setMessage("已读取当前空间的个人复习分析；完成时间代表最后一次保存为非未看状态。");
    } catch (error) {
      if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "读取失败"}；未展示旧分析，请手动重试。`);
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="learning-analysis" aria-label="个人复习分析" aria-busy={busy}>
    <span className="progress-kicker">REVIEW ANALYSIS</span><h2>个人复习分析</h2>
    <p>只统计当前空间中你自己的笔记学习记录。时间窗口使用服务器 UTC；已完成表示保存过非“未看”状态的记录，不是复习事件次数。</p>
    <button className="button secondary" disabled={busy} onClick={() => void refresh()}>刷新个人复习分析</button>
    {analysis && <>
      <p>统计时间：<time dateTime={analysis.as_of}>{new Date(analysis.as_of).toLocaleString()}</time>（本机时区）</p>
      <dl className="learning-counts" aria-label="个人复习分析统计">
        <div><dt>笔记总数</dt><dd>{analysis.total_notes}</dd></div>
        <div><dt>已保存学习记录</dt><dd>{analysis.recorded_notes}</dd></div>
        <div><dt>已完成记录</dt><dd>{analysis.completed_notes}</dd></div>
        <div><dt>近7天完成</dt><dd>{analysis.completed_last_7_days}</dd></div>
        <div><dt>近30天完成</dt><dd>{analysis.completed_last_30_days}</dd></div>
        {readingStates.map(state => <div key={state}><dt>{readingStateNames[state]}</dt><dd>{analysis.states[state]}</dd></div>)}
        <div><dt>已到期提醒</dt><dd>{analysis.reminders.due}</dd></div>
        <div><dt>未来提醒</dt><dd>{analysis.reminders.upcoming}</dd></div>
      </dl>
      <p>完成时间是当前记录的最后一次非未看保存时间；系统没有复习事件历史，因此不显示复习次数、连续学习天数、趋势或掌握率。提醒只表示数据库中的时间，不代表通知已发送。</p>
      {analysis.total_notes === 0 && <p>当前空间还没有笔记；不会自动创建演示数据。</p>}
    </>}
    <p role="status" aria-label="个人复习分析状态">{message}</p>
  </section>;
}
