"use client";

import { useEffect, useRef, useState } from "react";
import { accountFetch as fetch } from "@/lib/account-fetch";

const model = "multilingual-minilm-l12-v2-quint8-avx2-e8f8c211-mean128-v1";

export default function SemanticIndex({ token, space, noteId, revisionId }: { token: string; space: string; noteId: string; revisionId: string }) {
  const [consent, setConsent] = useState(false), [busy, setBusy] = useState(false), [status, setStatus] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  return <section aria-label="本地向量索引">
    <h4>本地检索索引</h4><p>将当前修订的标题与正文送入部署内的CPU模型，最多128段、12288字。索引不发送到云端；旧版本保留，搜索仅采用最新修订。需要空间管理员权限。</p>
    <label><input type="checkbox" checked={consent} disabled={busy} onChange={event => { setConsent(event.target.checked); setStatus(""); }} />同意为当前固定修订建立本地向量索引</label>
    <button className="button secondary" disabled={busy || !consent} onClick={() => { void (async () => {
      if (pending.current) return;
      const controller = new AbortController(); pending.current = controller; setBusy(true); setStatus("");
      try {
        const response = await fetch(`/api/account/spaces/${space}/knowledge/semantic/index`, { method: "POST", signal: controller.signal, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ note_id: noteId, revision_id: revisionId, consent }) });
        const value = await response.json();
        if (!response.ok) throw new Error(value.error?.code === "FORBIDDEN" ? "仅空间管理员可建立索引" : value.error?.code ?? "索引失败");
        const data = value.data;
        if (data?.revision_id !== revisionId || data.note_id !== noteId || data.model !== model || !Number.isInteger(data.chunks) || data.chunks < 1 || data.chunks > 128 || !Number.isInteger(data.indexed_characters) || typeof data.truncated !== "boolean") throw new Error("索引响应无效");
        if (!controller.signal.aborted) { setConsent(false); setStatus(`${data.reused ? "已复用" : "已保存"} ${data.chunks} 段本地向量，覆盖 ${data.indexed_characters} / ${data.total_characters} 字${data.truncated ? "，已达到上限" : ""}。`); }
      } catch (error) { if (!controller.signal.aborted) setStatus(`${error instanceof Error ? error.message : "索引失败"}；可手动核对后重试，同版本成功索引会复用。`); }
      finally { if (!controller.signal.aborted) setBusy(false); if (pending.current === controller) pending.current = null; }
    })(); }}>建立当前修订索引</button>
    <p role="status" aria-label="本地索引状态">{status}</p>
  </section>;
}
