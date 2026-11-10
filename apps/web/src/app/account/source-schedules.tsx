"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { CalendarClock } from "lucide-react";
import { parseSchedule, parseSchedulePage, type SchedulePage, type SourceSchedule } from "@/lib/source-schedules";

type Source = { id: string; canonical_url: string };
export default function SourceSchedules({ token, space }: { token: string; space: string }) {
  const [page, setPage] = useState<SchedulePage | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [source, setSource] = useState("");
  const [interval, setInterval] = useState(1440);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("尚未加载同步计划。只刷新明确选择的公开来源，不连接平台账号或调用模型。");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function request(path: string, signal: AbortSignal, method = "GET", body?: object) {
    const response = await fetch(`/api/account/spaces/${space}/${path}`, {
      method, signal, cache: "no-store", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.code ?? "SCHEDULE_UNAVAILABLE");
    return result;
  }

  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setMessage("");
    try { await action(controller.signal); }
    catch (error) { if (!controller.signal.aborted) { setPage(null); setSources([]); setSource(""); setConsent(false); setMessage(`${error instanceof Error ? error.message : "读取失败"}。已清空旧状态，请重新读取核对；不会自动重试或恢复计划。`); } }
    finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }

  async function load(signal: AbortSignal) {
    setPage(null); setSources([]); setSource(""); setConsent(false);
    const result = parseSchedulePage((await request("sources/schedules", signal)).data);
    const candidates = await request("sources?status=accepted&limit=20", signal);
    if (!Array.isArray(candidates.data) || candidates.data.length > 20
      || candidates.data.some((row: Source) => !row || typeof row.id !== "string" || !/^[0-9a-f-]{36}$/.test(row.id) || typeof row.canonical_url !== "string")) throw new Error("INVALID_SOURCE_RESPONSE");
    if (signal.aborted) return;
    setPage(result); setSources(candidates.data); setSource(candidates.data[0]?.id ?? "");
    setMessage(`已读取 ${result.schedules.length} 个个人同步计划${candidates.next_cursor ? "；来源只显示最近20条" : ""}。${result.collector_enabled ? "公开采集开关已开放。" : "运维采集开关关闭：可以保存计划，到期仅记录未启用，不访问外网。"}`);
  }

  async function save(signal: AbortSignal, row?: SourceSchedule) {
    if ((!row || !row.active) && !consent) throw new Error("请先确认定时公开采集授权");
    const result = await request(`sources/schedules${row ? `/${row.id}` : ""}`, signal, row ? "PATCH" : "POST",
      row ? { expected_version: row.version, active: !row.active, consent: row.active ? false : consent } : { source_id: source, interval_minutes: interval, consent: true });
    parseSchedule(result.data);
    await load(signal);
    if (!signal.aborted) setMessage(row ? row.active ? "计划已暂停，尚未完成的该计划任务已取消。网络已发出的字节无法撤回。" : "计划已恢复，从新间隔开始，不补跑错过的周期。" : "已保存同步计划，不会立即采集；可随时暂停。相同来源和间隔重复保存不创建第二个计划。");
  }

  return <section className="source-schedules" aria-label="公开来源定时同步">
    <h2><CalendarClock size={20} />公开来源定时同步</h2>
    <p>每天、每周或自定义间隔（6小时–7天）。最多20个个人来源计划；沿用安全采集、配额和真实任务记录。没有平台收藏夹登录、自动分类或自动付费总结。</p>
    <button className="button secondary" disabled={busy} onClick={() => void run(load)}>读取同步计划与候选来源</button>
    {page && <>
      <form onSubmit={event => { event.preventDefault(); void run(signal => save(signal)); }}>
        <label>计划来源<select value={source} disabled={busy} onChange={event => { setSource(event.target.value); setConsent(false); }}><option value="">请选择已接受来源</option>{sources.map(row => <option value={row.id} key={row.id}>{row.canonical_url}</option>)}</select></label>
        <label>同步间隔<select value={[1440, 10080].includes(interval) ? interval : "custom"} disabled={busy} onChange={event => { setInterval(event.target.value === "custom" ? 360 : Number(event.target.value)); setConsent(false); }}><option value={1440}>每天</option><option value={10080}>每周</option><option value="custom">自定义分钟</option></select></label>
        {![1440, 10080].includes(interval) && <label>自定义分钟<input type="number" min={360} max={10080} value={interval} disabled={busy} onChange={event => { setInterval(Number(event.target.value)); setConsent(false); }} /></label>}
        <label className="schedule-consent"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} />我授权按此间隔访问所选公开来源，直到暂停；不授权模型调用</label>
        <button className="button primary" disabled={busy || !source || !consent || !Number.isSafeInteger(interval) || interval < 360 || interval > 10080}>保存公开来源计划</button>
      </form>
      <ul>{page.schedules.map(row => <li key={row.id}><div><strong>{sources.find(item => item.id === row.source_id)?.canonical_url ?? row.source_id}</strong><p>{row.active ? "已启用" : "已暂停"} · 每 {row.interval_minutes} 分钟 · 版本 {row.version}</p><small>下次计划时间：{new Date(row.next_run_at).toLocaleString()} · {row.last_error ?? "尚无调度错误"}</small></div><button className="button secondary" disabled={busy || !row.active && !consent} onClick={() => void run(signal => save(signal, row))}>{row.active ? "暂停此同步计划" : "确认授权并恢复计划"}</button></li>)}</ul>
      {!page.schedules.length && <p>尚无同步计划。保存后将等待第一个间隔，不会立即访问来源。</p>}
    </>}
    <p role="status" aria-label="同步计划状态">{message}</p>
  </section>;
}
