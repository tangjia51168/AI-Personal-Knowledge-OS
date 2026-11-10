"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";
import { Bell, BellOff, CheckCheck, Mail } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { notificationEmailLabels, parseNotificationPage, type NotificationPage } from "@/lib/notifications";

export default function Notifications({ space }: { space: string }) {
  const [page, setPage] = useState<NotificationPage | null>(null);
  const [email, setEmail] = useState(false);
  const [consent, setConsent] = useState(false);
  const [message, setMessage] = useState("尚未读取通知。默认关闭，到期通知须主动订阅。");
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  const testKey = useRef<string | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  async function request(signal: AbortSignal, suffix = "", method = "GET", body?: object) {
    const response = await fetch(`/api/account/spaces/${space}/learning/notifications${suffix}`, {
      method, signal, cache: "no-store", headers: { "Content-Type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.code ?? "NOTIFICATION_UNAVAILABLE");
    return result.data;
  }
  async function load(signal: AbortSignal) {
    setPage(null); setConsent(false);
    const result = parseNotificationPage(await request(signal));
    if (!signal.aborted) { setPage(result); setEmail(result.preference.email); }
  }
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller; setBusy(true); setMessage("");
    try { await action(controller.signal); }
    catch (error) { if (!controller.signal.aborted) { setPage(null); setEmail(false); setConsent(false); setMessage(`${error instanceof Error ? error.message : "通知请求失败"}。已清空旧数据，请重新读取；自测重试保留原请求编号，不自动发送。`); } }
    finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }
  async function save(signal: AbortSignal, enabled: boolean) {
    if (!page || enabled && !consent) return;
    await request(signal, "/preference", "PUT", { expected_version: page.preference.version, enabled, email: enabled && email, consent: enabled && consent });
    await load(signal);
    if (!signal.aborted) setMessage(enabled ? "提醒订阅已保存。后台约每分钟检查到期记录；站内通知持久保存，可手动刷新。" : "已关闭提醒并取消尚未发送的邮件。已授权发出的网络请求无法撤回；站内历史保留。");
  }
  async function test(signal: AbortSignal) {
    if (!consent) return;
    testKey.current ??= crypto.randomUUID();
    await request(signal, "/test", "POST", { idempotency_key: testKey.current, consent: true });
    testKey.current = null;
    await load(signal);
    if (!signal.aborted) setMessage("自测通知已持久保存。邮件启用时等待后台尝试；请刷新查看回执，SMTP 接受不等于邮箱送达。");
  }
  return <section aria-label="提醒与通知" className="notification-center">
    <header><div><p className="account-eyebrow">PERSONAL REMINDERS</p><h2><Bell size={21} />提醒与通知</h2><p>主动订阅、个人收件箱与可核对的投递记录。邮件只发给本人已验证邮箱，不含笔记正文。</p></div><button className="button secondary" disabled={busy} onClick={() => void run(async signal => { await load(signal); if (!signal.aborted) setMessage("已读取最近50条通知及订阅状态。"); })}>读取通知与订阅</button></header>
    {page && <><div className="notification-settings"><div><strong>{page.preference.enabled ? "提醒已订阅" : "提醒已关闭"}</strong><p>每个到期时间通知一次；深读完成后不再为该条创建到期提醒。更改到期时间可产生新提醒。</p><small>邮箱{page.email_verified ? "已验证" : "未验证"} · SMTP{page.smtp_configured ? "已配置" : "未配置"} · 订阅版本 {page.preference.version}</small></div><div>
      <label><input type="checkbox" checked={email} disabled={busy || !page.email_verified || !page.smtp_configured} onChange={event => { setEmail(event.target.checked); setConsent(false); }} /><Mail size={16} />同时发送邮件</label>
      <label><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} />我确认当前订阅选项，并同意创建本次自测通知</label>
      <div className="notification-actions"><button className="button primary" disabled={busy || !consent} onClick={() => void run(signal => save(signal, true))}>保存提醒订阅</button><button className="button secondary" disabled={busy || !page.preference.enabled} onClick={() => void run(signal => save(signal, false))}><BellOff size={15} />关闭提醒</button><button className="button secondary" disabled={busy || !consent || !page.preference.enabled} onClick={() => void run(test)}>发送本人自测</button></div></div></div>
      <ul className="notification-list">{page.data.map(row => <li key={row.id}><span className={`notification-dot ${row.read_at ? "is-read" : ""}`} aria-label={row.read_at ? "已读" : "未读"} /><div><strong>{row.kind === "test" ? "提醒渠道自测" : "学习复习已到期"}</strong><p>{notificationEmailLabels[row.email_state]}</p><small>{new Date(row.created_at).toLocaleString()}{row.reminder_at ? ` · 到期 ${new Date(row.reminder_at).toLocaleString()}` : ""}</small>{row.note_id && <p>笔记编号：{row.note_id} · 可在下方到期清单打开</p>}</div><button className="button secondary" disabled={busy || !!row.read_at} onClick={() => void run(async signal => { await request(signal, `/${row.id}/read`, "POST", {}); await load(signal); if (!signal.aborted) setMessage("已标记为已读，不改变学习状态。"); })}><CheckCheck size={15} />{row.read_at ? "已读" : "标为已读"}</button></li>)}</ul>
      {!page.data.length && <p>尚无通知。保存订阅后可发送本人自测，或等待已有学习提醒到期。</p>}{page.has_more && <p>仅显示最近50条；更早历史仍保存在数据库。</p>}
    </>}<p role="status" aria-label="通知状态">{message}</p><small>自测每天最多3次；每位成员最多5000条。暂停期间不新建通知，恢复时会检查仍到期且未通知的记录。尚无外部邮箱送达证明或推送渠道。</small>
  </section>;
}
