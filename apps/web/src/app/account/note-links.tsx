"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import NoteGraph from "./note-graph";

type Access = { token: string; space: string };
type NoteCandidate = { id: string; title: string; version: number; origin: string };
type NoteLink = {
  id: string; note_id: string; title: string; version: number;
  created_by: string; updated_by: string; created_at: string; updated_at: string;
  method: "manual"; confidence: null;
};
type Props = Access & {
  noteId: string;
  disabled: boolean;
  onBusyChange: (busy: boolean) => void;
  onOpen: (noteId: string) => Promise<void>;
};
const errors: Record<string, string> = {
  NOTE_LINK_SELF: "不能把笔记关联到自身。",
  NOTE_NOT_FOUND: "笔记不存在或当前无权访问。",
  NOTE_LINK_QUOTA_EXCEEDED: "其中一篇笔记已达到 50 个链接上限。",
  SPACE_LINK_QUOTA_EXCEEDED: "空间已达到 1000 个链接上限。",
  NOTE_LINK_INVENTORY_LIMIT: "笔记链接超出读取上限，请联系空间管理员。",
};

async function api<T>({ token, space }: Access, path: string, method = "GET"): Promise<T> {
  const response = await fetch(`/api/account/spaces/${space}/${path}`, {
    method, cache: "no-store", headers: { Authorization: `Bearer ${token}` },
  });
  const result = await response.json();
  if (!response.ok) {
    const code = result.error?.code ?? "REQUEST_FAILED";
    throw new Error(errors[code] ?? code);
  }
  return result.data as T;
}

export default function NoteLinks({ token, space, noteId, disabled, onBusyChange, onOpen }: Props) {
  const field = useId();
  const [links, setLinks] = useState<NoteLink[] | null>(null);
  const [candidates, setCandidates] = useState<NoteCandidate[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("正在读取人工链接。");
  const [graphVersion, setGraphVersion] = useState(0);
  const locked = disabled || busy;

  async function loadLinks() {
    const rows = await api<NoteLink[]>({ token, space }, `notes/${noteId}/links`);
    setLinks(rows);
    setGraphVersion(value => value + 1);
    setSelected(value => rows.some(link => link.note_id === value) ? "" : value);
    setMessage("已加载双向人工链接。");
  }

  useEffect(() => {
    let active = true;
    void api<NoteLink[]>({ token, space }, `notes/${noteId}/links`)
      .then(rows => { if (active) { setLinks(rows); setMessage("已加载双向人工链接。"); } })
      .catch(error => { if (active) setMessage(error instanceof Error ? error.message : "读取链接失败"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [token, space, noteId]);

  async function act(action: () => Promise<void>) {
    if (locked) return;
    setBusy(true); onBusyChange(true);
    try { await action(); }
    catch (error) { setMessage(`${error instanceof Error ? error.message : "请求失败"}。可刷新链接核对结果；重复关联或移除不会创建重复记录。`); }
    finally { setBusy(false); onBusyChange(false); }
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    const params = new URLSearchParams({ limit: "20" });
    if (query.trim()) params.set("q", query.trim());
    const response = await fetch(`/api/account/spaces/${space}/notes?${params}`, {
      cache: "no-store", headers: { Authorization: `Bearer ${token}` },
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.code ?? "NOTE_SEARCH_FAILED");
    const linked = new Set((links ?? []).map(item => item.note_id));
    const options = (result.data as NoteCandidate[]).filter(
      note => note.id !== noteId && !linked.has(note.id),
    );
    setCandidates(options);
    setSelected(options.some(note => note.id === selected) ? selected : "");
    setMessage(`找到 ${options.length} 篇可关联笔记；仅显示前 20 条匹配结果。`);
  }

  async function update(target: string, method: "PUT" | "DELETE") {
    const rows = await api<NoteLink[]>({ token, space }, `notes/${noteId}/links/${target}`, method);
    setLinks(rows); setSelected("");
    setGraphVersion(value => value + 1);
    setCandidates(current => current.filter(note => note.id !== target));
    setMessage(method === "PUT" ? "已建立双向人工链接。" : "已移除双向人工链接；历史审计仍保留。");
  }

  return <section aria-label="笔记双向链接">
    <h4>人工双向链接</h4>
    <p>链接属于整篇笔记，任一端都能看到；查看或新增版本不会改变链接。它只表示成员手动建立的“相关”入口，不代表支持、反驳、因果、事实核验或模型置信度。</p>
    <p>每篇笔记最多 50 个、每空间最多 1000 个。当前链接不带方向或语义类型；更细的知识关系与证据图谱尚未提供。</p>
    <button className="button secondary" disabled={locked} onClick={() => void act(loadLinks)}>刷新双向链接</button>
    {links && <>
      {links.length === 0 ? <p>本笔记尚未建立人工链接。</p> : <ul aria-label="已链接笔记">{links.map(link => <li key={link.id} style={{ marginBlock: 8, overflowWrap: "anywhere" }}>
        <span>{link.title} · 最新版本 {link.version} · 人工链接</span>{" "}
        <button className="button secondary" disabled={locked} aria-label={`打开关联笔记：${link.title}`} onClick={() => void act(() => onOpen(link.note_id))}>打开</button>{" "}
        <button className="button secondary" disabled={locked} aria-label={`移除关联笔记：${link.title}`} onClick={() => void act(() => update(link.note_id, "DELETE"))}>移除链接</button>
      </li>)}</ul>}
      <form aria-label="搜索关联笔记" onSubmit={event => void act(() => search(event))}>
        <label htmlFor={`${field}-query`}>关联笔记关键词</label>{" "}
        <input id={`${field}-query`} value={query} disabled={locked} onChange={event => setQuery(event.target.value)} />{" "}
        <button className="button secondary" disabled={locked} type="submit">搜索可关联笔记</button>
      </form>
      {candidates.length > 0 && <p><label htmlFor={`${field}-target`}>可关联笔记</label>{" "}
        <select id={`${field}-target`} value={selected} disabled={locked} onChange={event => setSelected(event.target.value)}>
          <option value="">选择一篇笔记</option>
          {candidates.map(note => <option key={note.id} value={note.id}>{note.title} · 版本 {note.version}</option>)}
        </select>{" "}
        <button className="button secondary" disabled={locked || !selected || links.length >= 50} onClick={() => void act(() => update(selected, "PUT"))}>建立双向链接</button>
      </p>}
    </>}
    {disabled && <p>当前其他笔记操作期间暂停修改链接。</p>}
    <p role="status" aria-label="笔记链接状态">{message}</p>
    <NoteGraph key={`${space}-${noteId}-${graphVersion}`} token={token} space={space} noteId={noteId} disabled={locked} onOpen={id => act(() => onOpen(id))} />
  </section>;
}
