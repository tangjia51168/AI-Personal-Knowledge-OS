"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";

type Choice = { note_id: string; revision_id: string; title: string };

export default function BatchExport({ token, space, current }: { token: string; space: string; current: Choice | null }) {
  const [items, setItems] = useState<Choice[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); }, []);
  const selected = current && items.some(item => item.note_id === current.note_id && item.revision_id === current.revision_id);
  async function download() {
    if (active.current || !items.length) return;
    const controller = new AbortController();
    active.current = controller; setBusy(true); setMessage("");
    try {
      const response = await fetch(`/api/account/spaces/${space}/notes/export-batch`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ items: items.map(({ note_id, revision_id }) => ({ note_id, revision_id })) }), cache: "no-store", signal: controller.signal });
      if (!response.ok) { const result = await response.json(); throw new Error(result.error?.code ?? "BATCH_EXPORT_FAILED"); }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      if (blob.size > 8 * 1_048_576) throw new Error("NOTE_BATCH_LIMIT");
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = "knowledge-obsidian-batch.zip";
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(`已下载 ${items.length} 个明确选择的固定修订；不是完整备份。`);
    } catch (error) {
      if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "导出失败"}；未生成下载文件，请手动重试。`);
    } finally { if (active.current === controller) { active.current = null; setBusy(false); } }
  }
  return <section aria-label="Obsidian 批量导出">
    <h3>Obsidian 批量导出</h3>
    <p>先打开笔记并选择版本，再加入清单；跨页保留选择，版本不会自动更新。最多20个修订、总包8 MiB、每修订1 MiB。导出来源、证据、原文和SHA-256清单；不含其他业务数据，不是完整备份。</p>
    <button className="button secondary" disabled={busy || !current || !!selected || items.length >= 20} onClick={() => { if (current) setItems([...items, current]); }}>加入当前修订到批量导出</button>
    <ol>{items.map(item => <li key={item.revision_id}>{item.title} · 修订 {item.revision_id} <button disabled={busy} onClick={() => setItems(items.filter(row => row.revision_id !== item.revision_id))}>移出批量清单</button></li>)}</ol>
    <button className="button secondary" disabled={busy || !items.length} onClick={() => void download()}>下载选中修订 Obsidian ZIP</button>{" "}
    <button className="button secondary" disabled={busy || !items.length} onClick={() => { setItems([]); setMessage(""); }}>清空批量清单</button>
    <p role="status">{message}</p>
  </section>;
}
