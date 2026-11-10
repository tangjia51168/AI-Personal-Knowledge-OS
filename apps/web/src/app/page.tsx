"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import {
  ArrowDownToLine, ArrowRight, BookOpen, Box, Check, ChevronDown,
  CircleHelp, Command, Database, ExternalLink, FileText, FolderOpen,
  GitBranch, Inbox, Layers3, LayoutDashboard, ListTodo, LoaderCircle,
  Menu, Moon, Network, RefreshCw, Server, Settings2, ShieldCheck,
  Sparkles, Sun, Terminal, Workflow, X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { subscribeTheme, readTheme, toggleTheme } from "@/lib/theme";

type View = "overview" | "inbox" | "tasks" | "settings";
type Status = { status: "up" | "down" };
type System = { api: Status; dependencies: Record<string, Status> | null; checkedAt: string };

const navigation: { id: View; label: string; icon: LucideIcon }[] = [
  { id: "overview", label: "工作台", icon: LayoutDashboard },
  { id: "inbox", label: "内容收件箱", icon: Inbox },
  { id: "tasks", label: "任务中心", icon: ListTodo },
];
const infrastructure = [
  { key: "api", title: "API 服务", detail: "FastAPI · /health/live", icon: Server },
  { key: "postgresql", title: "知识数据库", detail: "PostgreSQL · pgvector", icon: Database },
  { key: "redis", title: "消息队列", detail: "Redis · Celery broker", icon: Workflow },
  { key: "storage", title: "对象存储", detail: "S3 compatible · MinIO", icon: Box },
];

async function fetchSystem(signal?: AbortSignal): Promise<System> {
  const response = await fetch("/api/system", { cache: "no-store", signal });
  if (!response.ok) throw new Error("状态检测请求失败");
  return (await response.json()).data;
}

export default function Home() {
  const [view, setView] = useState<View>("overview");
  const [system, setSystem] = useState<System | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => "light");
  const [menuOpen, setMenuOpen] = useState(false);
  const [showGuide, setShowGuide] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchSystem(controller.signal).then((result) => {
      if (!controller.signal.aborted) { setSystem(result); setError(""); }
    }).catch(() => {
      if (!controller.signal.aborted) setError("状态检测失败，请检查前端服务后重试。");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);

  async function refresh() {
    setLoading(true);
    try { setSystem(await fetchSystem()); setError(""); }
    catch { setSystem(null); setError("状态检测失败，请检查前端服务后重试。"); }
    finally { setLoading(false); }
  }

  const healthy = infrastructure.filter(({ key }) => (
    key === "api" ? system?.api.status : system?.dependencies?.[key]?.status
  ) === "up").length;
  const title = view === "settings" ? "系统设置" : navigation.find((item) => item.id === view)?.label;

  return <div className="app-shell">
    {menuOpen && <button className="mobile-shade" aria-label="关闭导航" onClick={() => setMenuOpen(false)} />}
    <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
      <Link className="brand" href="/" aria-label="Knowledge OS 首页">
        <span className="brand-mark"><Layers3 size={22} /></span>
        <span>Knowledge<span className="brand-os">OS</span><small>YOUR SECOND BRAIN</small></span>
      </Link>
      <div className="workspace-label"><span className="workspace-avatar">K</span><span>工程工作空间<small>本地开发环境</small></span><ChevronDown size={15}/></div>
      <p className="nav-caption">工作空间</p>
      <nav aria-label="主导航">
        <Link className="nav-item" href="/progress"><GitBranch size={19}/><span>项目进度与成果</span></Link>
        {navigation.map(({ id, label, icon: Icon }) => <button key={id}
          className={`nav-item ${view === id ? "active" : ""}`} aria-current={view === id ? "page" : undefined}
          onClick={() => { setView(id); setMenuOpen(false); }}>
          <Icon size={19}/><span>{label}</span>{id === "overview" && <span className="nav-dot"/>}
        </button>)}
        <p className="nav-caption future-caption">知识与学习 <span>登录使用</span></p>
        {[{ icon: BookOpen, label: "知识笔记", href: "/account#knowledge" }, { icon: Network, label: "证据研究与问答", href: "/account#research" }, { icon: ListTodo, label: "学习与复习", href: "/account#learning" }, { icon: ArrowDownToLine, label: "导出中心", href: "/account#exports" }].map(({ icon: Icon, label, href }) => <Link className="nav-item" key={label} href={href}><Icon size={19}/><span>{label}</span></Link>)}
        {[{ icon: FolderOpen, label: "收藏与合集" }].map(({ icon: Icon, label }) =>
          <div className="nav-item future" key={label} title="后续阶段实现"><Icon size={19}/><span>{label}</span><span className="soon">规划中</span></div>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="phase-card"><span className="phase-icon"><GitBranch size={17}/></span><strong>第一版 · 本机知识工作区</strong><p>资料、证据、学习和导出连通</p><small>完整主线及生产门禁仍按清单推进</small><Link href="/progress">查看项目进度与成果 →</Link></div>
        <Link className="nav-item" href="/account"><ShieldCheck size={19}/><span>账户与工作空间</span></Link>
        <button className={`nav-item ${view === "settings" ? "active" : ""}`} onClick={() => { setView("settings"); setMenuOpen(false); }}><Settings2 size={19}/><span>系统设置</span></button>
        <button className="nav-item" onClick={() => setShowGuide(true)}><CircleHelp size={19}/><span>启动指南</span><ExternalLink size={13}/></button>
        <div className="local-footer"><span className="tiny-dot"/> Local preview <span>V1</span></div>
      </div>
    </aside>

    <main className="main">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-toggle" aria-label="打开导航" onClick={() => setMenuOpen(true)}><Menu size={20}/></button><span>个人知识操作系统</span><span className="slash">/</span><strong>{title}</strong></div><div className="topbar-actions"><span className="environment"><span className="tiny-dot"/> 本地开发</span><button className="icon-button" onClick={toggleTheme} aria-label={theme === "light" ? "切换深色主题" : "切换浅色主题"}>{theme === "light" ? <Moon size={18}/> : <Sun size={18}/>}</button><Link className="user-avatar" title="打开账户与工作空间" href="/account">K</Link></div></header>
      <div className="content">
        <div className="page-heading"><div><div className="eyebrow">PERSONAL KNOWLEDGE, CONNECTED.</div><h1>{view === "overview" ? "让收藏，成为真正的知识。" : title}</h1><p>{view === "overview" ? "从每一次灵感出发，建立属于你的长期知识资产。" : "工程基础阶段 · 所有可用状态均来自真实服务。"}</p></div><button className="button secondary" onClick={() => setShowGuide(true)}><Terminal size={16}/>启动指南<ArrowRight size={15}/></button></div>

        {view === "overview" && <>
          <section className="notice" aria-label="最新交付"><GitBranch size={18}/><p>登录后可进入全部10个组件：导入资料、整理证据、研究问答、复习与八格式导出。<Link href="/progress">查看项目进度与模块成果 →</Link>；各功能范围和权限在页面说明。</p></section>
          <section className="hero-card"><div className="hero-copy"><span className="hero-badge"><span/> V1 · YOUR KNOWLEDGE WORKSPACE</span><h2>从一份素材，<br/>开始积累自己的知识。</h2><p>导入资料、保留原文、建立证据笔记。<br/>再用检索、复习与导出，让每次阅读都有后续。</p><Link className="button primary" href="/account">登录使用知识工作区<ArrowRight size={16}/></Link><button className="text-button" onClick={() => setShowGuide(true)}>查看启动步骤</button></div><div className="orbit-visual" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="orbit orbit-three"/><div className="orbit-center"><Layers3 size={36}/></div><div className="orbit-chip chip-one"><FileText size={17}/><span>原始内容</span></div><div className="orbit-chip chip-two"><Network size={17}/><span>关联知识</span></div><div className="orbit-chip chip-three"><Sparkles size={17}/><span>证据研究</span></div><span className="orbit-point p1"/><span className="orbit-point p2"/><span className="orbit-point p3"/></div></section>
          <div className="section-heading"><div><h2>基础服务状态 <span className="label">LIVE</span></h2><p>实时检查工程依赖；未连接的服务不会被标记为可用。</p></div><button className="text-button" disabled={loading} onClick={() => void refresh()}><RefreshCw size={14} className={loading ? "spinning" : ""}/>{loading ? "检测中" : "刷新状态"}</button></div>
          <div className="service-grid" aria-live="polite">{infrastructure.map(({ key, title: serviceTitle, detail, icon: Icon }) => {
            const status = key === "api" ? system?.api.status : system?.dependencies?.[key]?.status;
            return <article className="service-card" key={key}><div className="service-top"><div className={`service-icon ${key}`}><Icon size={20}/></div><span className={`status-pill ${loading ? "checking" : status === "up" ? "up" : "down"}`}><span/>{loading ? "检测中" : status === "up" ? "已连接" : "未连接"}</span></div><h3>{serviceTitle}</h3><p>{detail}</p></article>;
          })}</div>
          <div className="status-footnote">{error || (loading ? "正在向后端发送健康检查请求…" : `${healthy} / 4 项基础服务已连接`)}<span>{system && !loading ? `检测于 ${new Date(system.checkedAt).toLocaleTimeString("zh-CN", { hour12: false })}` : ""}</span></div>
          <div className="bottom-grid"><section className="panel"><div className="panel-heading"><h2>一条清晰的构建路径</h2><span className="subtle">分阶段交付</span></div><div className="milestone"><span className="milestone-marker current"><Terminal size={17}/></span><div><strong>01 <span>架构与工程基础</span></strong><p>应用骨架、基础设施、迁移、安全基线与测试。</p></div><span className="current-label">进行中</span></div><div className="milestone"><span className="milestone-marker"><Inbox size={17}/></span><div><strong>02 <span>内容采集 MVP</span></strong><p>多平台识别、分享链接解析与授权导入。</p></div></div><div className="milestone"><span className="milestone-marker"><Network size={17}/></span><div><strong>03 <span>理解、连接与复用</span></strong><p>证据型笔记、知识图谱与个人知识问答。</p></div></div></section><section className="panel principles"><span className="principle-icon"><ShieldCheck size={23}/></span><h2>知识资产，始终属于你。</h2><p>内容与模型解耦，来源与证据留存。<br/>从第一天开始，为隐私和可迁移性设计。</p><div><span><Check size={14}/>租户隔离优先</span><span><Check size={14}/>不伪造处理结果</span><span><Check size={14}/>模型无关架构</span></div><small>当前仅为开发工程，尚不具备公开上线条件。</small></section></div>
        </>}

        {view === "inbox" && <section className="panel empty-state"><span className="empty-icon"><Inbox size={34}/></span><span className="label">PHASE 1 · 来源与快照</span><h2>检查并保存分享链接</h2><p>登录账户后，可检查分享文本中的 URL、保存候选并查看入库记录。<br/>运维开启采集后，可逐次授权采集公开网页正文与视频元数据，查看内容快照并授权生成证据笔记；字幕、评论与弹幕尚未接入。</p><div className="empty-tags"><span>原始 URL</span><span>规范 URL</span><span>租户内去重</span><span>版本快照</span></div><Link className="button secondary" href="/account">打开账户收件箱<ArrowRight size={15}/></Link></section>}

        {view === "tasks" && <section className="panel task-panel"><div className="panel-heading"><h2>异步任务基础</h2><span className="label">PHASE 1</span></div><div className="pipeline"><span>FastAPI</span><ArrowRight size={16}/><span>Redis</span><ArrowRight size={16}/><span>Celery Worker</span><ArrowRight size={16}/><span>任务结果</span></div><p className="task-note">账户页支持空间元数据导出和公开内容采集任务，查看真实状态并取消或重试。任务经过空间授权和配额检查。</p><a className="button secondary" href="/account">打开账户与任务中心<ArrowRight size={15}/></a><div className="notice"><ShieldCheck size={18}/><p>平台拒绝访问时会记录真实错误；视频元数据不能替代字幕或完整视频总结。AI 笔记需另外确认内容发送与费用授权。</p></div></section>}

        {view === "settings" && <div className="settings-grid"><section className="panel setting-panel"><div className="panel-heading"><h2>外观设置</h2><Sun size={19}/></div><p>主题偏好保存在当前浏览器，不上传服务器。</p><button className="theme-option" onClick={toggleTheme}><span>{theme === "light" ? <Sun size={21}/> : <Moon size={21}/>}当前主题 · {theme === "light" ? "晨间浅色" : "静夜深色"}</span><span>切换<ArrowRight size={14}/></span></button></section><section className="panel setting-panel"><div className="panel-heading"><h2>模型与账户</h2><ShieldCheck size={19}/></div><p>账户页提供登录、空间偏好和加密模型配置。真实推理需配置授权供应商，并完成连接验收。</p><a className="button secondary" href="/account">打开账户设置<ArrowRight size={14}/></a></section><section className="panel setting-panel"><div className="panel-heading"><h2>工程连接</h2><Server size={19}/></div><p>服务端通过 API_BASE_URL 连接后端，部署配置见根目录 .env.example。</p><button className="text-button" onClick={() => { setView("overview"); void refresh(); }}>检测连接<RefreshCw size={14}/></button></section></div>}

        <footer className="page-footer"><span><Command size={13}/> Knowledge OS</span><span>内容有来源 · 知识有连接 · 思考可延续</span><span>Phase 1 / 内容与证据笔记</span></footer>
      </div>
    </main>

    {showGuide && <div className="modal-overlay" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) setShowGuide(false); }}><dialog open className="guide-modal" aria-labelledby="guide-title" onCancel={() => setShowGuide(false)}><div className="panel-heading"><h2 id="guide-title">启动工程环境</h2><button autoFocus className="icon-button" aria-label="关闭启动指南" onClick={() => setShowGuide(false)}><X size={20}/></button></div><p>安装 Docker Desktop（启用 Compose）后，在项目根目录执行：</p><ol><li>复制 .env.example 为 .env，替换所有 CHANGE_ME 密码与令牌。密码使用随机字母数字，避免 URL 转义歧义。</li><li><code>docker compose up --build -d</code></li><li><code>docker compose ps</code> 检查服务状态。</li><li>访问 localhost:3000，刷新基础服务检测。</li></ol><div className="notice"><ShieldCheck size={18}/><p>仅绑定本机地址。不要直接开放到公网。完整命令、测试方法和已知限制见根目录 README.md。</p></div><button className="button primary" onClick={() => setShowGuide(false)}>知道了<Check size={16}/></button></dialog></div>}
    {loading && <span className="sr-only"><LoaderCircle/> 正在检查服务</span>}
  </div>;
}
