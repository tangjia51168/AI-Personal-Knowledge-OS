"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useState } from "react";

export type KnowledgeLabel = { id: string; kind: "topic" | "tag"; name: string; created_at: string; active?: boolean; version?: number; updated_at?: string; parent_id?: string | null };
export const labelKindNames = { topic: "主题", tag: "标签" };
const errors: Record<string, string> = {
  LABEL_QUOTA_EXCEEDED: "空间分类已达到 100 个上限。",
  NOTE_LABEL_QUOTA_EXCEEDED: "本笔记已达到 20 个关联分类上限。",
  LABEL_NOT_FOUND: "分类不存在或当前无权访问。",
  NOTE_NOT_FOUND: "笔记不存在或当前无权访问。",
  LABEL_INVENTORY_LIMIT: "分类记录超出读取上限，请联系空间管理员。",
  LABEL_VERSION_CONFLICT: "分类已被其他成员修改，请刷新后重试。",
  LABEL_NAME_CONFLICT: "同类型分类名称已存在。",
  LABEL_ARCHIVED: "分类已归档，恢复后才能关联新笔记。",
  LABEL_UPDATE_EMPTY: "请提供名称或状态变更。",
};

type Access = { token: string; space: string };
type Props = Access & {
  noteId: string; disabled: boolean;
  onCatalogChanged: (labels: KnowledgeLabel[]) => void;
  onBusyChange: (busy: boolean) => void;
  onChanged: () => Promise<void>;
};

async function api<T>({ token, space }: Access, path: string, method = "GET", body?: object): Promise<T> {
  const response = await fetch(`/api/account/spaces/${space}/${path}`, {
    method, cache: "no-store", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const result = await response.json();
  if (!response.ok) {
    const code = result.error?.code ?? "REQUEST_FAILED";
    throw new Error(errors[code] ?? code);
  }
  return result.data as T;
}

export function loadLabelCatalog(access: Access): Promise<KnowledgeLabel[]> {
  return api<KnowledgeLabel[]>(access, "labels/manage");
}

export function normalizedName(value: string): string {
  if (/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value)) throw new Error("分类名称不能包含控制、换行或不可见格式字符。");
  const name = value.normalize("NFKC").replace(/\s+/gu, " ").trim();
  if (!name || Array.from(name).length > 60 || new TextEncoder().encode(name).length > 240) throw new Error("分类名称须为 1–60 字符，且不超过 240 字节 UTF-8。");
  return name;
}

export default function NoteLabels({ token, space, noteId, disabled, onCatalogChanged, onBusyChange, onChanged }: Props) {
  const fieldId = useId();
  const [catalog, setCatalog] = useState<KnowledgeLabel[]>([]);
  const [assigned, setAssigned] = useState<KnowledgeLabel[] | null>(null);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<KnowledgeLabel["kind"]>("topic");
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("正在读取笔记分类。");
  const locked = disabled || busy;

  useEffect(() => {
    let active = true;
    onBusyChange(true);
    void Promise.all([loadLabelCatalog({ token, space }), api<KnowledgeLabel[]>({ token, space }, `notes/${noteId}/labels`)])
      .then(([labels, linked]) => {
        if (!active) return;
        setCatalog(labels); setAssigned(linked); onCatalogChanged(labels);
        setMessage("已加载真实笔记分类。");
      })
      .catch(error => { if (active) setMessage(error instanceof Error ? error.message : "读取分类失败"); })
      .finally(() => { if (active) { setBusy(false); onBusyChange(false); } });
    return () => { active = false; onBusyChange(false); };
  }, [token, space, noteId, onCatalogChanged, onBusyChange]);

  async function act(action: () => Promise<void>) {
    if (locked) return;
    setBusy(true); onBusyChange(true); setMessage("");
    try { await action(); }
    catch (error) { setMessage(`${error instanceof Error ? error.message : "请求失败"}。可刷新分类核对结果；重试创建或关联不会重复保存相同分类。`); }
    finally { setBusy(false); onBusyChange(false); }
  }

  async function refresh() {
    const [labels, linked] = await Promise.all([loadLabelCatalog({ token, space }), api<KnowledgeLabel[]>({ token, space }, `notes/${noteId}/labels`)]);
    setCatalog(labels); setAssigned(linked); onCatalogChanged(labels);
    setSelected(value => labels.some(label => label.id === value) && !linked.some(label => label.id === value) ? value : "");
    setMessage("已加载真实笔记分类。");
  }

  async function update(labelId: string, method: "PUT" | "DELETE") {
    const linked = await api<KnowledgeLabel[]>({ token, space }, `notes/${noteId}/labels/${labelId}`, method);
    setAssigned(linked); setSelected("");
    const saved = method === "PUT" ? "分类已关联到笔记。" : "已移除本笔记的分类关联，空间分类仍保留。";
    setMessage(saved);
    try { await onChanged(); }
    catch { setMessage(`${saved}笔记列表刷新失败，请刷新列表核对；关联已保存。`); }
  }

  return <section aria-label="笔记人工分类">
    <h4>人工主题与标签</h4>
    <p>分类由空间成员共享，关联到整篇笔记，查看历史或保存新版本都会保留。相同分类不代表内容相互支持、因果关系或事实已核验。</p>
    <p>每个空间最多 100 个分类（含归档），每篇笔记最多关联 20 个。重命名、归档和恢复见分类目录维护；合并与删除尚未提供。</p>
    <button className="button secondary" disabled={locked} onClick={() => void act(refresh)}>刷新笔记分类</button>
    {assigned && <>
      <ul aria-label="已关联分类">{assigned.map(label => <li key={label.id} style={{ marginBlock: 8, overflowWrap: "anywhere" }}>
        <span>{labelKindNames[label.kind]}：{label.name}{catalog.find(item => item.id === label.id)?.active === false && "（已归档）"}</span>{" "}
        <button className="button secondary" disabled={locked} aria-label={`移除${labelKindNames[label.kind]}：${label.name}`} onClick={() => void act(() => update(label.id, "DELETE"))}>移除关联</button>
      </li>)}</ul>
      {assigned.length === 0 && <p>本笔记尚未关联主题或标签。</p>}
      <p><label htmlFor={`${fieldId}-existing`}>已有主题或标签</label>{" "}
        <select id={`${fieldId}-existing`} disabled={locked} value={selected} onChange={event => setSelected(event.target.value)}>
          <option value="">选择空间内的分类</option>
          {catalog.filter(label => label.active !== false && !assigned.some(linked => linked.id === label.id)).map(label => <option key={label.id} value={label.id}>{labelKindNames[label.kind]}：{label.name}</option>)}
        </select>{" "}
        <button className="button secondary" disabled={locked || !selected || assigned.length >= 20} onClick={() => void act(() => update(selected, "PUT"))}>关联到笔记</button>
      </p>
      <form aria-label="创建笔记分类" onSubmit={event => { event.preventDefault(); void act(async () => {
        const label = await api<KnowledgeLabel>({ token, space }, "labels", "POST", { kind, name: normalizedName(name) });
        const alreadyAssigned = assigned.some(linked => linked.id === label.id);
        setName(""); setSelected(alreadyAssigned ? "" : label.id);
        const labels = await loadLabelCatalog({ token, space });
        setCatalog(labels); onCatalogChanged(labels);
        setMessage(`分类已保存到空间；同类型规范化重名会复用已有分类。${alreadyAssigned ? "该分类已关联到本笔记。" : "请选择关联到笔记。"}`);
      }); }}>
        <label htmlFor={`${fieldId}-kind`}>新分类类型</label>{" "}<select id={`${fieldId}-kind`} value={kind} disabled={locked} onChange={event => setKind(event.target.value as KnowledgeLabel["kind"])}><option value="topic">主题</option><option value="tag">标签</option></select>{" "}
        <label htmlFor={`${fieldId}-name`}>新分类名称</label>{" "}<input id={`${fieldId}-name`} value={name} disabled={locked} onChange={event => setName(event.target.value)} />{" "}
        <button className="button secondary" type="submit" disabled={locked || !name.trim()}>创建分类</button>
      </form>
    </>}
    {disabled && <p>当前操作期间暂停修改分类；人工修订草稿会保留。</p>}
    <p role="status" aria-label="笔记分类状态">{message}</p>
  </section>;
}
