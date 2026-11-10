"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useRef, useState } from "react";
import { readerDocument, readerEvidence, sectionNames, type ReaderEvidence, type ReaderItem } from "../../lib/note-reader";

export default function NoteReader({ token, space, noteId, revision }: {
  token: string; space: string; noteId: string;
  revision: { id: string; snapshot_id: string; origin: string; document: Record<string, unknown> };
}) {
  const document = readerDocument(revision.document);
  const prefix = useId();
  const active = useRef<AbortController | null>(null);
  const pendingTarget = useRef<string | null>(null);
  const root = useRef<HTMLElement | null>(null);
  const [evidence, setEvidence] = useState<ReaderEvidence | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("尚未加载原文证据。点击引用编号可读取当前修订的已保存证据。");
  useEffect(() => () => { active.current?.abort(); }, []);
  function locate(name: string) {
    const element = globalThis.document.getElementById(`${prefix}-${name}`);
    if (element && root.current?.contains(element)) { element.focus(); element.scrollIntoView({ block: "center", behavior: "auto" }); }
  }
  useEffect(() => {
    if (evidence && pendingTarget.current) { locate(pendingTarget.current); pendingTarget.current = null; }
  });
  async function load(key?: string) {
    if (active.current || !document) return;
    if (evidence && key) { locate(key); return; }
    const controller = new AbortController(); active.current = controller;
    pendingTarget.current = key ?? null;
    setEvidence(null); setBusy(true); setMessage("正在读取当前修订的原文证据…");
    try {
      const response = await fetch(`/api/account/spaces/${space}/notes/${noteId}/revisions/${revision.id}/export?format=json`, {
        cache: "no-store", signal: controller.signal, headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("EVIDENCE_READ_FAILED");
      const text = await response.text();
      if (controller.signal.aborted) return;
      if (new TextEncoder().encode(text).length > 1_048_576) throw new Error("EVIDENCE_LIMIT");
      const keys = [document.overview, ...document.sections.flatMap(section => section.items), ...(document.stages ?? []).flatMap(stage => [stage.overview, ...stage.sections.flatMap(section => section.items)])].flatMap(item => item.evidence_keys);
      const result = readerEvidence(JSON.parse(text), noteId, revision.id, revision.snapshot_id, keys);
      if (!result) throw new Error("EVIDENCE_REVISION_INVALID");
      setEvidence(result); setMessage("已加载当前修订的真实证据。引用编号只定位原文，不证明观点正确。");
    } catch { if (!controller.signal.aborted) { pendingTarget.current = null; setMessage("原文证据读取失败；未展示旧证据，可手动重试。"); } }
    finally { if (!controller.signal.aborted) { active.current = null; setBusy(false); } }
  }
  if (!document) return <p role="status">此版本的结构化内容无法安全展示，请查看原始 Markdown 或导出文件。</p>;
  const missing = Object.keys(sectionNames).filter(kind => !document.sections.some(section => section.kind === kind));
  function paragraph(item: ReaderItem) {
    return <><p className="reader-text">{item.text}</p><div className="reader-citations">{item.evidence_keys.map(key => <button key={key} className="button secondary" disabled={busy} aria-label={`定位原文证据 ${key}`} onClick={() => void load(key)}>{key}</button>)}</div></>;
  }
  return <article ref={root} className="note-reader" aria-label="结构化笔记阅读">
    <p className="reader-kicker">结构化阅读 · 已保存修订</p>
    <nav aria-label="笔记章节目录">{[["overview", "概述"], ...document.sections.map(section => [section.kind, sectionNames[section.kind]]), ["limits", "缺失信息与限制"], ...(document.personal !== null ? [["personal", "个人理解"]] : [])].map(([key, label]) => <button key={key} className="button secondary" onClick={() => locate(key)}>{label}</button>)}</nav>
    <div className="reader-workspace"><div className="reader-document">
    <section id={`${prefix}-overview`} tabIndex={-1} aria-label="概述"><h5>概述</h5>{paragraph(document.overview)}</section>
    {document.sections.map(section => <section key={section.kind} id={`${prefix}-${section.kind}`} tabIndex={-1} aria-label={sectionNames[section.kind]}><h5>{sectionNames[section.kind]}</h5><ol>{section.items.map((item, index) => <li key={index}>{paragraph(item)}</li>)}</ol></section>)}
    {document.stages && <details className="long-analysis"><summary>分段分析记录 · {document.stages.length} 段</summary><p>全部片段已逐段发送；综合阶段读取分段概述与有界条目。以下记录保留分段细节及引用，不代表事实核验。</p>{document.stages.map((stage, index) => <section key={index} aria-label={`第 ${index + 1} 段分析`}><h5>第 {index + 1} 段</h5>{paragraph(stage.overview)}{stage.sections.map(section => <div key={section.kind}><h6>{sectionNames[section.kind]}</h6><ol>{section.items.map((item, i) => <li key={i}>{paragraph(item)}</li>)}</ol></div>)}<ul>{stage.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></section>)}</details>}
    <section id={`${prefix}-limits`} tabIndex={-1} aria-label="缺失信息与限制"><h5>缺失信息与限制</h5>
      {missing.length > 0 && <p>未生成类别（没有足够的结构化输出）：{missing.map(kind => sectionNames[kind]).join("、")}。</p>}
      <ul>{document.limitations.map((value, index) => <li key={index}>{value}</li>)}</ul>
      <p>{revision.origin === "user_edit" ? "人工修订沿用的证据范围" : "模型输入的证据范围"}：{document.coverage.provided_segments}/{document.coverage.total_segments} 个片段，本版本引用 {document.coverage.cited_segments} 个。这不是事实核验或语义覆盖率评分。</p>
      {document.coverage.partial && <p>仅涵盖快照前缀，其余内容尚未分析，不能视为全文总结。</p>}
      <p>未验证原链接、评论、弹幕或音视频；不生成或猜测视频时间点。</p>
    </section>
    {document.personal !== null && <section id={`${prefix}-personal`} tabIndex={-1} aria-label="个人理解"><h5>个人理解（未经来源或事实核验）</h5><p className="reader-text">{document.personal || "本版本未追加个人理解。"}</p></section>}
    </div><section className="reader-evidence-panel" aria-label="原文证据定位"><h5>原文证据</h5>
      <button className="button secondary" disabled={busy} onClick={() => void load()}>加载原文证据</button>
      <p role="status" aria-label="原文证据状态">{message}</p>
      {evidence && <><p>{evidence.provenance === "user_import" ? "人工导入（未核验），导入时未访问关联地址。" : "已保存的公开平台快照；本次未重新访问。"}</p><p className="reader-text">原始来源地址（仅文本）：{evidence.original}</p>
        <ul>{evidence.limitations.map((value, index) => <li key={index}>{value}</li>)}</ul>
        {evidence.quotes.map(quote => <section key={quote.key} id={`${prefix}-${quote.key}`} tabIndex={-1} aria-label={`原文证据 ${quote.key}`}><h6>{quote.key}</h6><blockquote className="reader-text">{quote.quote}</blockquote></section>)}
      </>}
    </section>
    </div>
  </article>;
}
