"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useId, useState } from "react";

type Item = { text: string; evidence_keys: string[] };
type Section = { kind: string; items: Item[] };
type EditableDocument = { title: string; overview: Item; sections: Section[]; personal_notes: string };
type EditResult = { note_id: string; revision_id: string; version: number };
type Props = {
  token: string; space: string; noteId: string;
  revision: { id: string; title: string; version: number; document: Record<string, unknown> };
  onSaved: (result: EditResult) => Promise<void>;
  onCancel: () => void;
  onOpenLatest: () => Promise<void>;
};

const sectionLabels: Record<string, string> = {
  core_claims: "核心结论与观点", steps: "操作步骤与方法", examples: "案例与场景",
  numbers: "数字与条件", takeaways: "可复用启发",
};

function item(value: unknown): Item | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  if (typeof record.text !== "string" || !Array.isArray(record.evidence_keys) || !record.evidence_keys.every(key => typeof key === "string")) return null;
  return { text: record.text, evidence_keys: [...record.evidence_keys] };
}

function editable(revision: Props["revision"]): EditableDocument | null {
  const overview = item(revision.document.overview);
  if (!overview || !Array.isArray(revision.document.sections)) return null;
  const sections: Section[] = [];
  for (const value of revision.document.sections) {
    if (typeof value !== "object" || value === null) return null;
    const record = value as Record<string, unknown>;
    if (typeof record.kind !== "string" || !(record.kind in sectionLabels) || !Array.isArray(record.items)) return null;
    const items = record.items.map(item);
    if (items.some(value => value === null)) return null;
    sections.push({ kind: record.kind, items: items as Item[] });
  }
  return { title: revision.title, overview, sections, personal_notes: typeof revision.document.personal_notes === "string" ? revision.document.personal_notes : "" };
}

export default function NoteEditor({ token, space, noteId, revision, onSaved, onCancel, onOpenLatest }: Props) {
  const fieldId = useId();
  const [draft, setDraft] = useState(() => editable(revision));
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [saved, setSaved] = useState<EditResult | null>(null);
  const [message, setMessage] = useState("");

  function changed(next: EditableDocument) {
    setDraft(next); setKey(crypto.randomUUID());
    if (!conflict) setMessage("");
  }

  async function submit() {
    if (!draft || saved || conflict) return;
    setBusy(true); setMessage("");
    try {
      if (!draft.title.trim() || Array.from(draft.title).length > 200) throw new Error("请填写不超过 200 字符的笔记标题。");
      if ([draft.overview, ...draft.sections.flatMap(section => section.items)].some(value => !value.text.trim() || Array.from(value.text).length > 1200)) throw new Error("概述与每条笔记内容须为 1–1,200 字符。");
      if (Array.from(draft.personal_notes).length > 4000) throw new Error("个人理解最多 4,000 字符。");
      const body = JSON.stringify({ idempotency_key: key, base_revision_id: revision.id, ...draft });
      if (new TextEncoder().encode(body).length > 65536) throw new Error("修订内容超过 64KiB UTF-8，请缩短后保存。");
      if (new TextEncoder().encode(body).length > 128 * 1024) throw new Error("修订请求超过 128KiB，请缩短后保存。");
      const response = await fetch(`/api/account/spaces/${space}/notes/${noteId}/revisions`, {
        method: "POST", cache: "no-store", body,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      });
      const result = await response.json();
      if (!response.ok) {
        const code = result.error?.code ?? "REQUEST_FAILED";
        if (response.status === 409 && code === "NOTE_REVISION_CONFLICT") {
          setConflict(true);
          setMessage("已有更新版本，本次未保存。草稿已保留；请复制需要保留的内容，再明确放弃草稿并打开最新版本。");
          return;
        }
        throw new Error(code);
      }
      const edit = result.data as EditResult;
      setSaved(edit);
      setMessage(`人工修订已保存为版本 ${edit.version}，正在刷新。`);
      try { await onSaved(edit); }
      catch { setMessage(`人工修订已保存为版本 ${edit.version}，刷新失败。请刷新已保存版本，无需再次提交。`); }
    } catch (error) {
      setMessage(`${error instanceof Error ? error.message : "请求失败"}。草稿已保留；未编辑内容时，重试使用原幂等键。`);
    } finally { setBusy(false); }
  }

  async function refreshSaved() {
    if (!saved) return;
    setBusy(true);
    try { await onSaved(saved); }
    catch { setMessage(`版本 ${saved.version} 已保存，刷新仍失败，请稍后重试。`); }
    finally { setBusy(false); }
  }

  if (!draft) return <section aria-label="笔记人工修订">
    <p>此版本没有可编辑的结构化章节，无法开始人工修订；原笔记仍可查看和下载。</p>
    <button className="button secondary" onClick={onCancel}>关闭修订</button>
  </section>;

  return <section aria-label="笔记人工修订">
    <h4>人工修订 · 基于版本 {revision.version}</h4>
    <p>保存将新增版本，原版本与来源快照保留，不会调用模型。可修订已有章节文字；引用编号与来源引文保留，文字是否得到引文支持仍需你核对。</p>
    <p>个人理解单独标注为未经来源核验，不会当作原文证据。草稿只保存在本页内存，离开或刷新页面会丢失。</p>
    <fieldset disabled={busy || saved !== null} style={{ border: 0, padding: 0, minWidth: 0 }}>
      <label htmlFor={`${fieldId}-title`}>修订标题</label>
      <input id={`${fieldId}-title`} value={draft.title} maxLength={200} onChange={event => changed({ ...draft, title: event.target.value })} style={{ display: "block", width: "100%", padding: 8, marginBlock: 8 }} />
      <label htmlFor={`${fieldId}-overview`}>修订概述</label>
      <textarea id={`${fieldId}-overview`} value={draft.overview.text} maxLength={1200} rows={4} onChange={event => changed({ ...draft, overview: { ...draft.overview, text: event.target.value } })} style={{ display: "block", width: "100%", padding: 8, marginBlock: 8 }} />
      <p>概述引用：{draft.overview.evidence_keys.join("、")}</p>
      {draft.sections.map((section, sectionIndex) => <section key={section.kind} aria-label={`修订章节 ${sectionLabels[section.kind]}`}>
        <h5>{sectionLabels[section.kind]}</h5>
        {section.items.map((entry, itemIndex) => <div key={itemIndex}>
          <label htmlFor={`${fieldId}-${section.kind}-${itemIndex}`}>{sectionLabels[section.kind]} 第 {itemIndex + 1} 条</label>
          <textarea id={`${fieldId}-${section.kind}-${itemIndex}`} value={entry.text} maxLength={1200} rows={3} onChange={event => changed({
            ...draft, sections: draft.sections.map((value, index) => index === sectionIndex ? {
              ...value, items: value.items.map((current, position) => position === itemIndex ? { ...current, text: event.target.value } : current),
            } : value),
          })} style={{ display: "block", width: "100%", padding: 8, marginBlock: 8 }} />
          <p>来源引用：{entry.evidence_keys.join("、")}</p>
        </div>)}
      </section>)}
      <label htmlFor={`${fieldId}-personal`}>个人理解（未经来源核验）</label>
      <textarea id={`${fieldId}-personal`} value={draft.personal_notes} maxLength={4000} rows={5} onChange={event => changed({ ...draft, personal_notes: event.target.value })} style={{ display: "block", width: "100%", padding: 8, marginBlock: 8 }} />
      <p><button className="button primary" disabled={conflict} onClick={() => void submit()}>保存为新版本</button></p>
    </fieldset>
    {saved && <button className="button secondary" disabled={busy} onClick={() => void refreshSaved()}>刷新已保存版本</button>}
    {conflict && <p><button className="button secondary" disabled={busy} onClick={async () => {
      setBusy(true);
      try { await onOpenLatest(); }
      catch { setMessage("打开最新版本失败，草稿仍保留，请稍后重试。"); }
      finally { setBusy(false); }
    }}>放弃草稿并打开最新版本</button></p>}
    <button className="button secondary" disabled={busy} onClick={onCancel}>{saved ? "关闭已保存修订" : "放弃草稿"}</button>
    <p role="status" aria-label="人工修订状态">{message}</p>
  </section>;
}
