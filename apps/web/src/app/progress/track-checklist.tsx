"use client";

import { useState } from "react";
import { tracks } from "@/lib/project-progress";
import { referenceRecord, requirementStateNames, trackAcceptance, trackVerification, type RequirementState } from "@/lib/track-acceptance";

export default function TrackChecklist() {
  const [selected, setSelected] = useState("organization");
  const [state, setState] = useState<RequirementState | "all">("all");
  const track = trackAcceptance.find(item => item.track === selected)!;
  const requirements = track.requirements.filter(item => state === "all" || item.state === state);
  return <section className="progress-section track-checklist" aria-labelledby="acceptance-title">
    <span className="progress-kicker">TRACK ACCEPTANCE</span><h2 id="acceptance-title">主线需求与验收清单</h2>
    <p>按完整目标逐项核对。已有范围的历史验收记录不代表整条主线通过；全部必需项、真实流程及维护门禁通过后，才可验收整条主线。</p>
    <details><summary>参考资料与核实情况</summary><p>{referenceRecord.document}，{referenceRecord.paragraphs} 个非空段落。{referenceRecord.status}</p><p>段落编号从0开始；原文件 SHA-256：<code>{referenceRecord.sha256}</code>。详细映射：<code>docs/mainline-acceptance.md</code>。</p></details>
    <div className="track-checklist-controls"><label>选择验收主线<select aria-label="选择验收主线" value={selected} onChange={event => { setSelected(event.target.value); setState("all"); }}>{tracks.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>验收项状态<select aria-label="验收项状态" value={state} onChange={event => setState(event.target.value as RequirementState | "all")}><option value="all">全部验收项</option>{Object.entries(requirementStateNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label></div>
    <h3>{tracks.find(item => item.id === selected)!.name} · 整体未验收</h3><p>{track.goal}</p><p>验收流程：{track.workflow}</p>
    <details><summary>本轮已实现范围的验收结果</summary><p>真实集成：{trackVerification[selected].integration}。</p><p>浏览器：{trackVerification[selected].browser}。</p><p>未完成：{trackVerification[selected].remaining}。各集合可能共用测试，不累加为项目进度；报告索引见 <code>docs/mainline-acceptance.md</code>。</p></details>
    <p>前置能力：{track.dependencies.length ? track.dependencies.map(id => tracks.find(item => item.id === id)!.name).join("、") : "无其他业务主线前置"}。前置范围须具体核对，无需等待相关主线全部完成。</p>
    <p role="status" aria-label="验收清单筛选结果">显示 {requirements.length} / {track.requirements.length} 个验收项；这是清单数量，不是完成率。</p>
    {requirements.length === 0 && <p>当前状态没有验收项。</p>}
    <ol className="track-requirements" aria-label="主线验收项">{requirements.map(item => <li key={item.id} id={item.id}><div className="progress-card-heading"><h4>{item.title}</h4><span className={`progress-badge ${item.state}`}>{requirementStateNames[item.state]}</span></div><p>验收标准：{item.acceptance}</p><p>缺口与限制：{item.gap}</p><p className="progress-muted">文档段落 [{item.paragraphs[0]}–{item.paragraphs[1]}] · {item.id}</p></li>)}</ol>
    <p>维护依据：<code>{track.document}</code>。固定入口：<code>.venv/Scripts/python.exe scripts/track_acceptance.py --track {track.track} --list</code>；仅列出本机已实现范围的检查，不触发模型或平台请求。</p>
  </section>;
}
