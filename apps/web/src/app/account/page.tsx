"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ArrowRight, BookOpen, Eye, EyeOff, Files as FilesIcon, Inbox as InboxIcon, LayoutGrid, ListTodo, Network, ScrollText, Settings2, ShieldCheck } from "lucide-react";
import { isAccountSection } from "@/lib/account-sections";
import Jobs from "./jobs";
import Files from "./files";
import Models from "./models";
import Audit from "./audit";
import Recovery from "./recovery";
import SecurityEvents from "./security-events";
import Inbox from "./inbox";
import Knowledge from "./knowledge";
import Learning from "./learning";
import LearningSummary from "./learning-summary";
import LearningSchedule from "./learning-schedule";
import LearningAnalysis from "./learning-analysis";
import Research from "./research";
import ExportCenter from "./export-center";
import ResearchAnswer from "./research-answer";
import SourceSchedules from "./source-schedules";
import Notifications from "./notifications";
import Collections from "./collections";
import { FolderHeart } from "lucide-react";
import "./workspace.css";
import { subscribeTheme, readTheme, toggleTheme } from "@/lib/theme";

type Space = { id: string; name: string; role: string };

export default function Account() {
  // A non-secret mount identity, never a bearer. The BFF uses an HttpOnly cookie.
  const [token, setToken] = useState("");
  const [checking, setChecking] = useState(true);
  const [expiresAt, setExpiresAt] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [activeSection, setActiveSection] = useState("overview");
  const sessionGeneration = useRef(0);
  const sessionChannel = useRef<BroadcastChannel | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const selectedSpace = useMemo(() => spaces.find(space => space.id === selected), [spaces, selected]);
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => "light");
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);

  useEffect(() => {
    if (!token || !selected) return;
    function scrollToSection() {
      if (isAccountSection(window.location.hash)) {
        const section = window.location.hash.slice(1);
        setActiveSection(section);
        window.requestAnimationFrame(() => document.getElementById(section)?.scrollIntoView({ block: "start" }));
      }
    }
    scrollToSection();
    window.addEventListener("hashchange", scrollToSection);
    return () => window.removeEventListener("hashchange", scrollToSection);
  }, [token, selected]);

  useEffect(() => {
    let alive = true;
    let running = false;
    const controller = new AbortController();
    function clearSession() {
      if (!alive) return;
      sessionGeneration.current++;
      setToken(""); setSpaces([]); setSelected(""); setExpiresAt("");
      setMessage("登录已过期或被撤销，请重新登录。");
    }
    async function restore() {
      if (running || !alive) return;
      running = true;
      const generation = sessionGeneration.current;
      try {
        const response = await fetch("/api/account/auth/me", { cache: "no-store", signal: controller.signal });
        if (response.status === 401) return;
        if (!response.ok) throw new Error("无法核验登录状态，请重试。");
        const { data } = await response.json();
        if (!data?.expires_at || Date.parse(data.expires_at) <= Date.now()) { clearSession(); return; }
        const listResponse = await fetch("/api/account/spaces", { cache: "no-store", signal: controller.signal });
        if (!listResponse.ok) throw new Error("无法读取空间，请刷新重试。");
        const list = (await listResponse.json()).data as Space[];
        if (!alive || generation !== sessionGeneration.current) return;
        setExpiresAt(data.expires_at); setToken(`${data.id}:${data.expires_at}`);
        setSpaces(list); setSelected(current => list.some(space => space.id === current) ? current : list[0]?.id ?? "");
      } catch (error) {
        if (alive && !controller.signal.aborted) setMessage(error instanceof Error ? error.message : "会话核验失败，请刷新重试。");
      } finally { running = false; if (alive) setChecking(false); }
    }
    const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel("knowledge-session");
    sessionChannel.current = channel;
    function onVisible() { if (document.visibilityState === "visible") void restore(); }
    window.addEventListener("knowledge-session-expired", clearSession);
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    if (channel) channel.onmessage = () => { clearSession(); void restore(); };
    void restore();
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void restore(); }, 60000);
    return () => {
      alive = false; controller.abort(); window.clearInterval(timer); channel?.close();
      if (sessionChannel.current === channel) sessionChannel.current = null;
      window.removeEventListener("knowledge-session-expired", clearSession);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  useEffect(() => {
    if (!expiresAt) return;
    const timer = window.setTimeout(() => window.dispatchEvent(new Event("knowledge-session-expired")), Math.max(0, Date.parse(expiresAt) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [expiresAt]);

  function notifySessionChange() {
    sessionChannel.current?.postMessage("changed");
  }

  async function request(path: string, method = "GET", body?: object) {
    const response = await fetch(`/api/account/${path}`, {
      method, cache: "no-store", headers: {
        "Content-Type": "application/json",
      }, ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.code ?? "REQUEST_FAILED");
    return result.data;
  }

  async function authenticate(mode: "login" | "register") {
    sessionGeneration.current++;
    setBusy(true); setMessage("");
    try {
      const result = await request(`auth/${mode}`, "POST", { email, password });
      if (result.verification_required) {
        setPassword(""); setMessage("请先查看验证邮件并完成邮箱验证，再登录。"); return;
      }
      const profile = await request("auth/me");
      setPassword(""); setShowPassword(false); setExpiresAt(profile.expires_at); setToken(`${profile.id}:${profile.expires_at}`);
      const list = await request("spaces");
      setSpaces(list); setSelected(list[0]?.id ?? "");
      notifySessionChange();
      setMessage("已登录，所有组件共用此会话；12 小时后需要重新登录。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "请求失败"); }
    finally { setBusy(false); }
  }

  async function chooseSpace(id: string) {
    try {
      const space = await request(`spaces/${id}`);
      setSelected(space.id); setMessage(`当前空间：${space.name}（${space.role}）`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "切换失败"); }
  }

  async function logout() {
    try { await request("auth/logout", "POST"); }
    catch { setMessage("注销请求失败，请重试或等待会话过期。"); return; }
    sessionGeneration.current++;
    setToken(""); setExpiresAt(""); setSpaces([]); setSelected(""); setMessage("会话已撤销。"); notifySessionChange();
  }

  const moduleCards = [
    { id: "inbox", label: "内容收件箱", detail: "保存链接、导入授权正文并查看来源快照。", icon: InboxIcon },
    { id: "collections", label: "收藏与合集", detail: "收纳已有素材，整理课程与专题，维护共享顺序。", icon: FolderHeart },
    { id: "knowledge", label: "知识库与研究", detail: "检索证据、阅读版本、维护主题、链接和学习状态。", icon: BookOpen },
    { id: "research", label: "证据研究", detail: "按关键词定位固定修订，缺资料时明确提示。", icon: Network },
    { id: "exports", label: "导出中心", detail: "选择固定版本，下载八种格式或批量 Obsidian 包。", icon: FilesIcon },
    { id: "learning", label: "学习与复习", detail: "状态、提醒、计划、日程和复习分析。", icon: ListTodo },
    { id: "files", label: "私有文件", detail: "上传、下载和删除空间资产。", icon: FilesIcon },
    { id: "jobs", label: "任务中心", detail: "查看后台任务状态、结果和重试边界。", icon: Network },
    { id: "models", label: "模型设置", detail: "管理员配置供应商；调用始终需要明确授权。", icon: Settings2 },
    { id: "security", label: "账户安全", detail: "查看自己的登录、验证和会话安全历史。", icon: ShieldCheck },
    { id: "audit", label: "空间审计", detail: "管理员查看关键变更；成员仍受后端授权限制。", icon: ScrollText },
  ];

  return <main className="account-page">
    <header className="account-header">
      <div><Link href="/" className="account-back">← 返回工作台</Link><p className="account-eyebrow">PERSONAL KNOWLEDGE OS · V1 WORKSPACE</p><h1>账户与工作空间</h1><p>登录后从一个工作台访问所有已实现组件。真实数据、权限和任务状态仍由后端接口决定。</p></div>
      <Link className="button secondary" href="/progress">查看项目进度 <ArrowRight size={15} /></Link>
      <button className="button secondary" onClick={toggleTheme} aria-label={theme === "light" ? "切换深色主题" : "切换浅色主题"}>{theme === "light" ? "深色主题" : "浅色主题"}</button>
    </header>
    {checking ? <p role="status">正在核验登录状态…</p> : !token ? <form onSubmit={(event) => { event.preventDefault(); void authenticate("login"); }}>
      <div className="login-symbol"><ShieldCheck size={28} /></div><h2>欢迎回到知识工作区</h2><p>一次登录，访问全部组件。会话有效期为 12 小时。</p>
      <p><label>电子邮箱<input type="email" required autoComplete="username" value={email} onChange={event => setEmail(event.target.value)} style={{ display: "block", padding: 10, width: "100%" }} /></label></p>
      <div className="password-field"><label htmlFor="account-password">密码（12–256 字符）</label><div><input id="account-password" type={showPassword ? "text" : "password"} required minLength={12} maxLength={256} autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} /><button type="button" aria-label={showPassword ? "隐藏密码" : "显示密码"} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={19} /> : <Eye size={19} />}</button></div></div>
      <button className="button primary" disabled={busy} type="submit">登录</button>{" "}
      <button className="button secondary" disabled={busy || !email || password.length < 12} type="button" onClick={() => void authenticate("register")}>注册并创建个人空间</button>
    </form> : <section key={token} className={`account-workspace ${activeSection !== "overview" ? "has-sidebar" : ""}`}>
      <div id="overview" className="account-overview">
        <div className="workspace-picker"><div><span className="account-eyebrow">CURRENT WORKSPACE</span><strong>{selectedSpace?.name ?? "选择工作空间"}</strong><small>{selectedSpace?.role ?? ""} · 登录有效至 {expiresAt ? new Date(expiresAt).toLocaleString() : "核验中"}</small></div><select aria-label="当前工作空间" value={selected} onChange={event => void chooseSpace(event.target.value)}>{spaces.map(space => <option key={space.id} value={space.id}>{space.name} · {space.role}</option>)}</select><button className="button secondary" onClick={() => void logout()}>退出并撤销会话</button></div>
        <div className="account-hero" hidden={activeSection !== "overview"}><div><span className="account-eyebrow">YOUR KNOWLEDGE WORKSPACE</span><h2>把素材变成可复用的知识</h2><p>从收件箱开始，沿着证据、版本、主题与复习链路继续工作。</p></div><div className="account-hero-orbit" aria-hidden="true"><Network size={46} /><span /><span /><span /></div></div>
        <nav className={`account-module-nav ${activeSection !== "overview" ? "is-compact" : ""}`} aria-label="已实现组件导航">{moduleCards.map(({ id, label, detail, icon: Icon }) => <a href={`#${id}`} key={id} aria-current={activeSection === id ? "page" : undefined} onClick={() => setActiveSection(id)}><Icon size={18} aria-hidden="true" /><span><strong>{label}</strong><small>{detail}</small></span><ArrowRight size={15} aria-hidden="true" /></a>)}</nav>
        {activeSection !== "overview" && <div className="workspace-breadcrumb"><a href="#overview" onClick={() => setActiveSection("overview")}><LayoutGrid size={16} /> 全部组件</a><span>/</span><strong>{moduleCards.find(card => card.id === activeSection)?.label}</strong></div>}
      </div>
      <div id="security" className="account-section" hidden={activeSection !== "security"}><SecurityEvents key={token} token={token} /></div>
      {selected && <div id="jobs" className="account-section" hidden={activeSection !== "jobs"}><Jobs key={selected} token={token} space={selected} /><SourceSchedules key={`schedules-${selected}-${token}`} token={token} space={selected} /></div>}
      {selected && <div id="inbox" className="account-section" hidden={activeSection !== "inbox"}><Inbox key={`inbox-${selected}`} token={token} space={selected} canSummarize={selectedSpace?.role !== "member"} /></div>}
      {selected && <div id="collections" className="account-section" hidden={activeSection !== "collections"}><Collections key={`collections-${selected}-${token}`} space={selected} /></div>}
      {selected && <div id="knowledge" className="account-section" hidden={activeSection !== "knowledge"}><Knowledge key={`knowledge-${selected}`} token={token} space={selected} /></div>}
      {selected && <div id="research" className="account-section" hidden={activeSection !== "research"}><Research key={`research-${selected}`} token={token} space={selected} /><ResearchAnswer key={`answer-${selected}-${token}`} token={token} space={selected} canGenerate={selectedSpace?.role === "owner" || selectedSpace?.role === "admin"} /></div>}
      {selected && <div id="exports" className="account-section" hidden={activeSection !== "exports"}><ExportCenter key={`exports-${selected}-${token}`} token={token} space={selected} /></div>}
      {selected && <div id="learning" className="account-section" hidden={activeSection !== "learning"}><Notifications key={`notifications-${selected}-${token}`} space={selected} /><LearningSummary key={`summary-${selected}-${token}`} token={token} space={selected} /><LearningAnalysis key={`analysis-${selected}-${token}`} token={token} space={selected} /><LearningSchedule key={`schedule-${selected}-${token}`} token={token} space={selected} /><Learning key={`learning-${selected}`} token={token} space={selected} /></div>}
      {selected && <div id="files" className="account-section" hidden={activeSection !== "files"}><Files key={`files-${selected}`} token={token} space={selected} /></div>}
      {selected && <div id="models" className="account-section" hidden={activeSection !== "models"}>{selectedSpace?.role === "member" ? <section aria-label="模型设置"><h2>模型设置</h2><p>此组件对所有成员可见；供应商密钥、用量和设置仅空间管理员可读取和管理。你仍可阅读笔记、整理证据及导出。</p></section> : <Models key={`models-${selected}`} token={token} space={selected} />}</div>}
      {selected && <div id="audit" className="account-section" hidden={activeSection !== "audit"}>{selectedSpace?.role === "member" ? <section aria-label="空间审计"><h2>空间审计</h2><p>业务审计记录仅空间管理员可读取；当前成员权限不受界面入口影响。个人安全历史可在账户安全查看。</p></section> : <Audit key={`audit-${selected}`} token={token} space={selected} />}</div>}
    </section>}
    <p role="status" aria-label="账户状态">{message}</p>
    {!token && <Recovery />}
    <p>尚未开放公网注册：邮箱验证与找回密码已加入，生产邮件送达、TLS 与上线安全门禁仍需验收。</p>
  </main>;
}
