"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useState } from "react";
import { isAccountSection } from "@/lib/account-sections";

export default function Recovery() {
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [kind, setKind] = useState<"verify" | "reset">("verify");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    function readFragment() {
      if (!window.location.hash) return;
      if (isAccountSection(window.location.hash)) return;
      const values = new URLSearchParams(window.location.hash.slice(1));
      const mode = values.has("reset") ? "reset" : "verify";
      const value = values.get(mode) ?? "";
      window.history.replaceState(null, "", window.location.pathname);
      const valid = /^[A-Za-z0-9_-]{43}$/.test(value);
      setKind(mode); setToken(valid ? value : ""); setPassword("");
      setMessage(valid ? "链接已载入，请确认操作。" : "链接无效，请重新申请邮件。");
    }
    // Synchronize location after hydration and same-document mail-link navigation.
    // Loading a fragment never submits an API request; confirmation is explicit.
    readFragment();
    window.addEventListener("hashchange", readFragment);
    return () => window.removeEventListener("hashchange", readFragment);
  }, []);

  async function submit(path: string, body: object) {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`/api/account/auth/${path}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body), cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "RECOVERY_UNAVAILABLE");
      setMessage(result.data.message ?? (path === "verify-email" ? "邮箱已验证，请使用密码登录。" : "密码已更新，所有旧会话已撤销，请重新登录。"));
      setToken(""); setPassword("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "请求失败"); }
    finally { setBusy(false); }
  }

  return <section aria-label="邮箱验证与找回密码">
    <h2>邮箱验证与找回密码</h2>
    <p>申请邮件后，请打开邮件链接并确认操作。链接仅在短时间内有效，使用后失效。</p>
    <label>接收邮件的邮箱<input type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} style={{ display: "block", width: "100%", padding: 10 }} /></label>
    <p><button className="button secondary" disabled={busy || !email} onClick={() => void submit("request-verification", { email })}>重发验证邮件</button>{" "}
      <button className="button secondary" disabled={busy || !email} onClick={() => void submit("request-password-reset", { email })}>发送找回密码邮件</button></p>
    {token && <form onSubmit={event => { event.preventDefault(); void submit(kind === "verify" ? "verify-email" : "reset-password", kind === "verify" ? { token } : { token, password }); }}>
      {kind === "reset" && <p><label>新密码<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required value={password} onChange={event => setPassword(event.target.value)} style={{ display: "block", width: "100%", padding: 10 }} /></label></p>}
      <p><button className="button primary" disabled={busy} type="submit">{kind === "verify" ? "确认验证邮箱" : "确认重置密码"}</button></p>
    </form>}
    <p role="status">{message}</p>
  </section>;
}
