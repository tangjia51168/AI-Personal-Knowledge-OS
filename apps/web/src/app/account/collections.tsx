"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";
import { Bookmark, FolderHeart, Layers3, Archive, ArrowRight, RefreshCw, Link2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { parseCollections, parseCollectionDetail, type Collection, type CollectionDetail } from "@/lib/collections";

export default function Collections({ space }: { space: string }) {
  const [groups, setGroups] = useState<Collection[] | null>(null);
  const [detail, setDetail] = useState<CollectionDetail | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("folder");
  const [editName, setEditName] = useState("");
  const [source, setSource] = useState("");
  const [position, setPosition] = useState(0);
  const [message, setMessage] = useState("读取空间收藏与合集后开始整理。共享归集不会复制正文。");
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const base = `/api/account/spaces/${space}/collections`;
  async function request(signal: AbortSignal, suffix = "", method = "GET", body?: object) {
    const response = await fetch(base + suffix, { method, signal, cache: "no-store", headers: { "Content-Type": "application/json" }, ...(body ? {body: JSON.stringify(body)} : {}) });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.code ?? "COLLECTION_UNAVAILABLE");
    return value.data;
  }
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller; setBusy(true); setMessage("");
    try { await action(controller.signal); }
    catch (error) { if (!controller.signal.aborted) { setGroups(null); setDetail(null); setSource(""); setMessage(`${error instanceof Error ? error.message : "读取失败"}。旧结果已清空；写入结果不确定时先重新读取核对，不自动重试。`); } }
    finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }
  async function load(signal: AbortSignal) {
    setDetail(null); setGroups(null);
    const rows = parseCollections(await request(signal));
    if (!signal.aborted) setGroups(rows);
  }
  async function open(signal: AbortSignal, id: string) {
    setDetail(null); setSource("");
    const value = parseCollectionDetail(await request(signal, `/${id}/items`));
    if (!signal.aborted) { setDetail(value); setEditName(value.collection.name); }
  }
  async function setItem(signal: AbortSignal, id: string, enabled: boolean, order: number) {
    if (!detail) return;
    await request(signal, `/${detail.collection.id}/items/${id}`, "PUT", {expected_version: detail.collection.version, active: enabled, position: order});
    await open(signal, detail.collection.id);
    if (!signal.aborted) setMessage(enabled ? "素材归集与位置已保存。" : "已移出当前组，原素材和笔记保留。");
  }
  return <section aria-label="收藏与合集" className="collection-center">
    <header className="collection-header"><div><p className="account-eyebrow">YOUR COLLECTIONS</p><h2><FolderHeart size={25} />收藏与合集</h2><p>把已有素材收进收藏夹，或按位置整理为课程与专题合集。</p></div><button className="button secondary" disabled={busy} onClick={() => void run(load)}><RefreshCw size={16} />读取收藏与合集</button></header>
    <div className="collection-layout"><aside><form className="collection-create" onSubmit={event => {event.preventDefault(); void run(async signal => { await request(signal, "", "POST", {name, kind}); setName(""); await load(signal); if (!signal.aborted) setMessage("已创建空间共享分组。"); });}}><h3>新建分组</h3><label>分组类型<select value={kind} disabled={busy} onChange={event => setKind(event.target.value)}><option value="folder">收藏夹</option><option value="collection">自定义合集</option></select></label><label>分组名称<input value={name} maxLength={60} disabled={busy} onChange={event => setName(event.target.value)} required /></label><button className="button primary" disabled={busy || !groups || !name.trim()}>创建分组</button></form><details className="collection-roadmap"><summary>平台导入与同步规划</summary><p>当前可用：空间共享手工归集、排序、归档与恢复。</p><p>待实现：CSV/JSON收藏数据映射、官方授权收藏夹、公开合集分页和增量同步。普通URL导入不等于平台收藏夹导入。</p><a href="#inbox">先在收件箱保存素材 <ArrowRight size={14} /></a></details></aside>
    <div>{groups && <><div className="collection-cards">{groups.map(row => <button type="button" key={row.id} className={`collection-card ${detail?.collection.id === row.id ? "is-selected" : ""}`} disabled={busy} onClick={() => void run(signal => open(signal, row.id))}>{row.kind === "folder" ? <Bookmark size={24} /> : <Layers3 size={24} />}<strong>{row.name}</strong><span>{row.kind === "folder" ? "收藏夹" : "自定义合集"} · {row.active ? "使用中" : "已归档"}</span><small>打开素材 <ArrowRight size={13} /></small></button>)}</div>{!groups.length && <div className="collection-empty"><Bookmark size={34} /><h3>从第一组收藏开始</h3><p>为一个主题建收藏夹，再从收件箱复制已有素材编号加入。</p></div>}</>}
    {detail && <section className="collection-detail" aria-label="分组素材"><h3><Layers3 size={20} />{detail.collection.name}</h3><p>共享分组 · 版本 {detail.collection.version} · {detail.items.length} 项</p><form onSubmit={event => {event.preventDefault(); void run(async signal => {if (!detail) return; await request(signal, `/${detail.collection.id}`, "PATCH", {expected_version: detail.collection.version, name: editName, active: detail.collection.active}); await load(signal); if (!signal.aborted) setMessage("分组名称已保存，请重新打开。");});}}><label>修改分组名称<input value={editName} maxLength={60} disabled={busy} onChange={event => setEditName(event.target.value)} required /></label><button className="button secondary" disabled={busy}>保存名称</button><button type="button" className="button secondary" disabled={busy} onClick={() => void run(async signal => { await request(signal, `/${detail.collection.id}`, "PATCH", {expected_version: detail.collection.version, name: detail.collection.name, active: !detail.collection.active}); await load(signal); if (!signal.aborted) setMessage("分组状态已保存，原素材保留。");})}><Archive size={15} />{detail.collection.active ? "归档分组" : "恢复分组"}</button></form>
    {detail.collection.active && <form onSubmit={event => {event.preventDefault(); void run(signal => setItem(signal, source.trim(), true, position));}}><label>已有素材编号<input value={source} onChange={event => setSource(event.target.value)} placeholder="收件箱中的素材 UUID" pattern="[0-9a-f-]{36}" required disabled={busy} /></label><label>排列位置（0–199）<input type="number" min={0} max={199} value={position} disabled={busy} onChange={event => setPosition(Number(event.target.value))} required /></label><button className="button primary" disabled={busy}>加入当前组</button></form>}
    <ol className="collection-items">{detail.items.map(row => <li key={row.source_id}><div className="collection-order">{row.position}</div><div><Link2 size={15} /><span>{row.url}</span><small>{row.source_id}</small></div><div className="collection-item-actions"><button className="button secondary" disabled={busy || !detail.collection.active || row.position === 0} onClick={() => void run(signal => setItem(signal, row.source_id, true, row.position - 1))}>前移</button><button className="button secondary" disabled={busy || !detail.collection.active || row.position === 199} onClick={() => void run(signal => setItem(signal, row.source_id, true, row.position + 1))}>后移</button><button className="button secondary" disabled={busy} onClick={() => void run(signal => setItem(signal, row.source_id, false, row.position))}>移出</button></div></li>)}</ol>{!detail.items.length && <p>本组暂无素材。加入的是来源关联，不改变已有笔记版本。</p>}</section>}</div></div>
    <p role="status" aria-label="收藏状态">{message}</p><small>每空间最多100组，每组200个来源（含已移出记录）。同一素材可加入多个组；同位置按来源编号稳定排序。</small>
  </section>;
}
