"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, ArrowRight, Check, Layers3, ShieldCheck } from "lucide-react";
import { currentTrack, moduleResults, progressUpdated, statusNames, trackModules, tracks, type TrackStatus } from "@/lib/project-progress";
import TrackChecklist from "./track-checklist";

export default function ProgressPage() {
  const [filter, setFilter] = useState<TrackStatus | "all">("all");
  const [selectedTrack, setSelectedTrack] = useState("all");
  const visible = tracks.filter(track => filter === "all" || track.status === filter);
  const modules = moduleResults.filter(item => selectedTrack === "all" || trackModules[selectedTrack]?.includes(item.id));
  return <main className="progress-page">
    <nav className="progress-nav" aria-label="进度页面导航"><Link href="/"><ArrowLeft size={16}/> 返回工作台</Link><Link href="/account">打开实际功能 <ArrowRight size={16}/></Link></nav>
    <header className="progress-hero">
      <span className="progress-kicker"><Layers3 size={18}/> KNOWLEDGE OS · DELIVERY LOG</span>
      <h1>项目进度与模块成果</h1>
      <p>每一项交付，都有可查看的结果。</p>
      <div className="progress-meta"><span>记录更新于 <time dateTime={progressUpdated}>{progressUpdated}</time></span><span>本机交付 · 未公开上线</span></div>
    </header>
    <aside className="progress-notice" aria-label="进度记录边界"><ShieldCheck size={22}/><div><h2>工程验收记录，不是实时业务数据</h2><p>按 PROGRESS.md、TASKS.md 和项目进度文档人工核对维护，随版本构建发布。测试数量不是项目完成百分比；不统计任何租户数据，也不读取本机日志或密钥。</p><p>完整 Phase 0 / Phase 1 尚未完成。当前镜像仍需漏洞门禁；用户恢复旧暂缓目标，暂无生产资源，先完成可部署版本。当前不能公开上线。</p></div></aside>
    <section className="progress-next" aria-label="当前实施主线"><span className="progress-kicker">CURRENT TRACK</span><h2>当前主线：{currentTrack.name}</h2><p>{currentTrack.stage} · {currentTrack.goal}</p><p>{currentTrack.acceptance}</p><p>后续范围：{currentTrack.remaining}</p><a href="#module-title" onClick={() => setSelectedTrack(currentTrack.id)}>查看本主线已有成果</a></section>
    <section className="progress-section" aria-labelledby="track-title">
      <div className="progress-section-heading"><div><span className="progress-kicker">01 / PROJECT MAP</span><h2 id="track-title">项目主线</h2></div><div><label htmlFor="track-filter">按状态查看</label> <select id="track-filter" value={filter} onChange={event => setFilter(event.target.value as TrackStatus | "all")}><option value="all">全部主线</option>{Object.entries(statusNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></div></div>
      <p className="progress-muted" role="status" aria-label="项目主线筛选结果">显示 {visible.length} / {tracks.length} 条主线。当前模块通过不等于整条产品主线完成。</p>
      <div className="progress-track-grid">{visible.map(track => <article className="progress-track" key={track.id} aria-labelledby={`track-${track.id}`}><div className="progress-card-heading"><h3 id={`track-${track.id}`}>{track.name}</h3><span className={`progress-badge ${track.status}`}>{statusNames[track.status]}</span></div><p>{track.result}</p><p className="progress-remaining">仍需完成／边界：{track.remaining}</p><a href="#module-title" onClick={() => setSelectedTrack(track.id)}>查看此主线的交付记录（{trackModules[track.id]?.length ?? 0}）</a></article>)}</div>
    </section>
    <TrackChecklist />
    <section className="progress-section" aria-labelledby="module-title">
      <div className="progress-section-heading"><div><span className="progress-kicker">02 / MODULE RESULTS</span><h2 id="module-title">已验收模块成果</h2></div><span className="progress-muted">按交付记录查看，不累计为总体完成率</span></div>
      <p className="progress-muted">每张卡片保留实际能力、查看步骤、验收时基线与未完成范围。下列数量是各次验收的历史快照，不代表本轮重新运行。功能入口需要登录；不会自动登录、生成演示数据或发起采集／AI请求。</p>
      <label htmlFor="module-track">按项目主线查看成果</label>{" "}<select id="module-track" value={selectedTrack} onChange={event => setSelectedTrack(event.target.value)}><option value="all">全部主线成果</option>{tracks.map(track => <option key={track.id} value={track.id}>{track.name}</option>)}</select>
      <p className="progress-muted" role="status" aria-label="主线成果筛选结果">显示 {modules.length} 项交付记录；同一项能力可服务多条主线，不重复计为总体进度。</p>
      {modules.length === 0 && <p>该主线尚无已验收交付；未完成范围见对应主线说明。</p>}
      <div className="progress-module-grid">{modules.map((module, index) => <article key={module.id} className="progress-module" id={`module-${module.id}`} aria-labelledby={`title-${module.id}`}>
        <div className="progress-card-heading"><span className="progress-module-number">{String(index + 1).padStart(2, "0")}</span><span className="progress-badge delivered"><Check size={12}/> 本机模块已验收</span></div>
        <h3 id={`title-${module.id}`}>{module.name}</h3><p>{module.result}</p>
        <details><summary>查看结果与验收边界</summary><dl><dt>如何查看</dt><dd>{module.steps}</dd><dt>验收时记录</dt><dd>{module.checks}</dd><dt>版本与范围</dt><dd>{module.baseline}</dd><dt>仍未提供</dt><dd>{module.limitation}</dd><dt>详细依据（仓库文件）</dt><dd><code>{module.document}</code> · 记录日期 {module.date}</dd></dl></details>
        <Link className="button secondary" href={`/account#${module.target}`}>打开{module.name}功能入口 <ArrowRight size={14}/></Link>
      </article>)}</div>
    </section>
    <section className="progress-next" aria-label="后续交付方式"><span className="progress-kicker">03 / WHAT COMES NEXT</span><h2>按项目主线实施，展示完整流程</h2><p>第一版本机工作区基础流程已连通，继续按完整主线清单补缺。历史模块保留为维护单元，不以卡片数量或测试数量计算完成率。页面变化附真实截图，协议夹具不作为平台采集或模型推理证据。</p><p>所有模块持续维护接口契约、回归测试、维护命令和已知限制。用户已恢复旧暂缓目标；分享页全文、第三方客户端和便携空间包继续实施，未取得实际结果不标完成。</p><p>尚未完成：长文一般语义质量、全库概念/方向关系、向量混合检索与高级研究、复习事件历史/永久题库/外部通知送达、收藏夹账号同步/自动建议、平台完整采集和生产门禁。已有本地语义检索、证据自测和站内提醒分别记录。基础RAG本轮没有新付费外部推理。主线整体完成与阶段交付分别记录。</p></section>
    <footer className="progress-footer">Knowledge OS · 内容有来源，交付有依据。<Link href="/">返回工作台</Link></footer>
  </main>;
}
