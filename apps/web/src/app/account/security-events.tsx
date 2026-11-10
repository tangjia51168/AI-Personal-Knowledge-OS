"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useState } from "react";

type SecurityEvent = { id: string; action: string; created_at: string };
const actions: Record<string, string> = {
  "account.registered": "账户注册", "login.succeeded": "登录成功",
  "login.failed": "针对账户的登录失败尝试", "login.unverified": "未验证邮箱的登录尝试",
  "session.logged_out": "撤销当前会话", "session.all_revoked": "撤销全部会话",
  "email.verified": "完成邮箱验证", "password.reset": "重置密码并撤销旧会话",
};

export default function SecurityEvents({ token }: { token: string }) {
  const [events, setEvents] = useState<SecurityEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("尚未加载账户安全历史。");

  async function load(append: boolean) {
    setBusy(true);
    try {
      const query = new URLSearchParams({ limit: "20" });
      if (append && cursor) query.set("cursor", cursor);
      const response = await fetch(`/api/account/auth/security-events?${query}`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "SECURITY_EVENTS_UNAVAILABLE");
      setEvents(previous => append ? [...previous, ...result.data] : result.data);
      setCursor(result.next_cursor ?? null);
      setMessage(result.data.length ? "已加载真实账户安全历史。" : "没有更多安全记录。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "账户安全历史加载失败"); }
    finally { setBusy(false); }
  }

  return <section aria-label="账户安全历史">
    <h2>账户安全历史</h2>
    <p>仅显示自己的账户事件，与当前工作空间无关。</p>
    <p>失败登录表示针对账户的尝试，不代表由你操作；同类尝试每 15 分钟合并一条，并非逐次计数。不包含限流前被拒绝的请求。</p>
    <button className="button secondary" disabled={busy} onClick={() => void load(false)}>刷新账户安全历史</button>
    <ol>{events.map(event => <li key={event.id} style={{ marginBlock: 12, overflowWrap: "anywhere" }}>
      <strong>{actions[event.action] ?? "未知安全事件"}</strong> · <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString()}</time>
    </li>)}</ol>
    {cursor && <button className="button secondary" disabled={busy} onClick={() => void load(true)}>加载更早安全记录</button>}
    <p role="status">{message}</p>
  </section>;
}
