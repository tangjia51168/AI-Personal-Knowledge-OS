"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";
import { type KnowledgeLabel, labelKindNames, normalizedName } from "./note-labels";
import { possibleTopicParents, topicPath } from "@/lib/topic-tree";

type Props = { token: string; space: string; disabled: boolean; onBusy: (busy: boolean) => void; onSaved: () => void; onCatalog: (labels: KnowledgeLabel[]) => void };
const errors: Record<string, string> = {
  LABEL_VERSION_CONFLICT: "分类已被其他成员修改，请重新读取目录后核对。",
  LABEL_NAME_CONFLICT: "同类型名称已存在（包括归档分类），请重新读取后核对。",
  LABEL_NOT_FOUND: "分类不存在或无权访问。",
  TOPIC_PARENT_CYCLE: "不能将主题放到自身或下级主题之下，请刷新后核对。",
  TOPIC_PARENT_ARCHIVED: "上级主题已归档，请选择启用的主题或移回根目录。",
  TOPIC_PARENT_NOT_FOUND: "上级主题不存在或不属于当前空间，请刷新后核对。",
};

export default function LabelManager({ token, space, disabled, onBusy, onSaved, onCatalog }: Props) {
  const [labels, setLabels] = useState<KnowledgeLabel[] | null>(null);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [active, setActive] = useState(true);
  const [parent, setParent] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("尚未读取分类目录。");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => { pending.current?.abort(); onBusy(false); }, [onBusy]);
  const label = labels?.find(item => item.id === selected);
  const locked = disabled || busy;

  async function act(save: boolean) {
    if (locked || pending.current) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); onBusy(true); setMessage("");
    try {
      const body = save && label ? { expected_version: label.version, name: normalizedName(name), active,
        ...(label.kind === "topic" && parent !== (label.parent_id ?? "") ? { parent_id: parent || null } : {}) } : null;
      const response = await fetch(`/api/account/spaces/${space}/labels/${body ? label!.id : "manage"}`, {
        method: body ? "PATCH" : "GET", cache: "no-store", signal: controller.signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(errors[result.error?.code] ?? result.error?.code ?? "分类请求失败");
      if (controller.signal.aborted) return;
      setSelected(""); setName(""); setConfirmed(false);
      if (body) {
        setLabels(null); onSaved();
        setMessage("分类维护已保存。请刷新分类目录核对；笔记分类与筛选名称也需重新读取。");
      } else {
        if (!Array.isArray(result.data) || result.data.length > 100 || result.data.some((item: KnowledgeLabel) => !item.id || !item.name || typeof item.active !== "boolean" || !Number.isInteger(item.version))) throw new Error("分类响应无效");
        for (const item of result.data) topicPath(result.data, item.id);
        setLabels(result.data); onCatalog(result.data); setMessage("已加载可维护的分类目录。");
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setLabels(null); setSelected(""); setConfirmed(false);
        setMessage(`${error instanceof Error ? error.message : "请求失败"}。${save ? "保存结果可能未知，" : ""}请刷新分类目录核对后再操作。`);
      }
    } finally {
      if (!controller.signal.aborted) { setBusy(false); onBusy(false); }
      if (pending.current === controller) pending.current = null;
    }
  }

  return <section aria-label="分类目录维护">
    <h3>分类目录维护</h3>
    <p>空间成员共享主题层级和标签；只有主题可选择同空间上级，禁止循环。重命名更新关联笔记显示；归档保留子主题/关联和筛选，但不能作为新上级。含归档最多100个，名称在同类型全空间内唯一。筛选仍只匹配明确关联的分类，不自动包含子主题。无合并、删除或自动分类。</p>
    <button className="button secondary" disabled={locked} onClick={() => void act(false)}>刷新分类目录</button>
    {labels && <>
      <ul aria-label="可维护分类">{labels.map(item => <li key={item.id}>{labelKindNames[item.kind]}：{topicPath(labels, item.id)} · {item.active ? "启用" : "已归档"}</li>)}</ul>
      {labels.length === 0 ? <p>当前空间没有分类；打开笔记后可创建。</p> : <form aria-label="维护分类" onSubmit={event => { event.preventDefault(); if (confirmed && label) void act(true); }}>
        <label>选择维护分类<select aria-label="选择维护分类" disabled={locked} value={selected} onChange={event => {
          const item = labels.find(row => row.id === event.target.value);
          setSelected(item?.id ?? ""); setName(item?.name ?? ""); setActive(item?.active !== false); setParent(item?.parent_id ?? ""); setConfirmed(false);
        }}><option value="">选择分类</option>{labels.map(item => <option key={item.id} value={item.id}>{labelKindNames[item.kind]}：{item.name}{!item.active && "（已归档）"}</option>)}</select></label>
        {label && <>
          <p><label>维护后的分类名称<input aria-label="维护后的分类名称" value={name} disabled={locked} onChange={event => { setName(event.target.value); setConfirmed(false); }} /></label></p>
          {label.kind === "topic" && <p><label>上级主题<select aria-label="上级主题" value={parent} disabled={locked} onChange={event => { setParent(event.target.value); setConfirmed(false); }}><option value="">无上级（根主题）</option>{labels.some(item => item.id === label.parent_id && !item.active) && <option value={label.parent_id!} disabled>{topicPath(labels, label.parent_id!)}（保留原上级）</option>}{possibleTopicParents(labels, label.id).map(item => <option key={item.id} value={item.id}>{topicPath(labels, item.id)}</option>)}</select></label></p>}
          <p><label><input type="checkbox" checked={active} disabled={locked} onChange={event => { setActive(event.target.checked); setConfirmed(false); }} />允许新的笔记关联（取消为归档）</label></p>
          <p><label><input type="checkbox" checked={confirmed} disabled={locked} onChange={event => setConfirmed(event.target.checked)} />确认更新这个共享分类，已有笔记关联保留</label></p>
          <button className="button secondary" disabled={locked || !confirmed || !name.trim() || (name === label.name && active === label.active && parent === (label.parent_id ?? ""))} type="submit">保存分类维护</button>
        </>}
      </form>}
    </>}
    <p role="status" aria-label="分类目录状态">{message}</p>
  </section>;
}
