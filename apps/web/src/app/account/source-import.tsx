"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useRef, useState, type ChangeEvent } from "react";

type Format = "text" | "srt" | "vtt";
type ContentKind = "article" | "transcript";
type Job = { id: string; status: string; error_code: string | null };
type Props = { token: string; space: string; sourceId: string; onChanged: () => Promise<void> };

export default function SourceImport({ token, space, sourceId, onChanged }: Props) {
  const fieldId = useId();
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [format, setFormat] = useState<Format>("text");
  const [contentKind, setContentKind] = useState<ContentKind>("article");
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [message, setMessage] = useState("");
  const reading = useRef(false);
  const fileGeneration = useRef(0);
  useEffect(() => () => { fileGeneration.current += 1; }, []);

  function changed() {
    setKey(crypto.randomUUID());
    setConsent(false);
    setMessage("");
  }

  async function readFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    event.target.value = "";
    if (!selected || reading.current || busy || job) return;
    if (selected.size > 65_536) { setMessage("文件最多 64KiB UTF-8，请分段后导入。"); return; }
    reading.current = true; setBusy(true); setConsent(false);
    const generation = ++fileGeneration.current;
    try {
      const value = new TextDecoder("utf-8", { fatal: true }).decode(await selected.arrayBuffer());
      if (new TextEncoder().encode(value).length > 65_536 || Array.from(value).length > 60_000) throw new Error("文件超过正文边界");
      if (generation !== fileGeneration.current) return;
      setText(value); setTitle(title || selected.name.replace(/\.[^.]+$/, "")); changed();
      setMessage(`已读取本机文件“${selected.name}”；点击授权并导入后才会发送到服务器。`);
    } catch { if (generation === fileGeneration.current) setMessage("文件不是可读取的 UTF-8 文本，或超过导入边界。"); }
    finally { reading.current = false; if (generation === fileGeneration.current) setBusy(false); }
  }

  async function api(path: string, body?: string): Promise<Job> {
    const response = await fetch(`/api/account/spaces/${space}/${path}`, {
      method: body ? "POST" : "GET", cache: "no-store",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      ...(body ? { body } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.code ?? "REQUEST_FAILED");
    return result.data as Job;
  }

  async function submit() {
    if (!consent || job || busy || reading.current) return;
    setBusy(true); setMessage("");
    try {
      if (!title.trim() || Array.from(title).length > 200) throw new Error("请填写不超过 200 字符的导入标题。");
      if (!text.trim() || Array.from(text).length > 60000 || new TextEncoder().encode(text).length > 65536) throw new Error("正文最多 60,000 字符且不超过 64KiB UTF-8，请分段后导入。");
      const body = JSON.stringify({ idempotency_key: key, consent: true, title, text, format, content_kind: contentKind });
      if (new TextEncoder().encode(body).length > 128 * 1024) throw new Error("含转义字符的导入请求超过 128KiB，请分段后导入。");
      const result = await api(`sources/${sourceId}/imports`, body);
      setJob(result);
      setMessage("导入任务已保存，后台将处理。请手动刷新状态与快照。");
    } catch (error) {
      setMessage(`${error instanceof Error ? error.message : "请求失败"}。未编辑内容时，重试保留原幂等键。`);
    } finally { setBusy(false); }
  }

  async function refresh() {
    if (!job) return;
    setBusy(true); setMessage("");
    try {
      const result = await api(`jobs/${job.id}`);
      setJob(result);
      if (result.status === "succeeded") {
        setMessage("人工导入快照已保存。内容由你提供，尚未核验与关联来源是否一致。");
        await onChanged();
      } else if (["failed", "cancelled"].includes(result.status)) {
        setMessage(`导入未完成：${result.error_code ?? result.status}。可在任务中心查看详情。`);
      } else setMessage("任务尚未完成，请稍后手动刷新。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "刷新失败"); }
    finally { setBusy(false); }
  }

  return <details>
    <summary>导入授权正文或字幕</summary>
    <section aria-label="授权正文与字幕导入">
      <p>粘贴你有权使用的正文或字幕，保存为人工导入快照。导入不会访问关联平台，也不会自动调用模型。</p>
      <p>支持纯文本、SRT 和 WebVTT；最多 60,000 字符且不超过 64KiB UTF-8。字幕时间标签会保留，内容与原平台的对应关系尚未核验。</p>
      <p>字幕仅支持按开始时间排序的基本时间段，暂不支持注释、样式块或字幕定位设置。</p>
      <label htmlFor={`${fieldId}-title`}>导入标题</label>
      <input id={`${fieldId}-title`} value={title} maxLength={200} disabled={busy || job !== null} onChange={event => { setTitle(event.target.value); changed(); }} style={{ display: "block", width: "100%", padding: 8 }} />
      <p><label htmlFor={`${fieldId}-format`}>导入格式</label><select id={`${fieldId}-format`} value={format} disabled={busy || job !== null} onChange={event => {
        const value = event.target.value as Format;
        setFormat(value);
        if (value !== "text") setContentKind("transcript");
        changed();
      }}>
        <option value="text">纯文本</option><option value="srt">SRT 字幕</option><option value="vtt">WebVTT 字幕</option>
      </select>{" "}<label htmlFor={`${fieldId}-kind`}>内容类型</label><select id={`${fieldId}-kind`} value={contentKind} disabled={busy || job !== null || format !== "text"} onChange={event => { setContentKind(event.target.value as ContentKind); changed(); }}>
        <option value="article">正文</option><option value="transcript">字幕／转录</option>
      </select></p>
      <label htmlFor={`${fieldId}-text`}>授权正文或字幕</label>
      <label htmlFor={`${fieldId}-file`}>或选择本机文本文件（TXT / MD / CSV / JSON）</label>
      <input id={`${fieldId}-file`} type="file" accept=".txt,.md,.csv,.json,text/plain,text/markdown,text/csv,application/json" disabled={busy || job !== null} onChange={event => void readFile(event)} />
      <p>文件按 UTF-8 纯文本读取；CSV / JSON 不会解析为多条来源，Markdown 也不执行。选择文件仅填入下方草稿。</p>
      <textarea id={`${fieldId}-text`} value={text} disabled={busy || job !== null} onChange={event => { setText(event.target.value); changed(); }} rows={8} style={{ display: "block", width: "100%", padding: 8, marginBlock: 12 }} />
      <input id={`${fieldId}-consent`} type="checkbox" checked={consent} disabled={busy || job !== null} onChange={event => setConsent(event.target.checked)} />
      <label htmlFor={`${fieldId}-consent`}>我有权使用这些内容，并同意保存人工导入快照</label>
      <p><button className="button primary" disabled={busy || !consent || !title.trim() || !text.trim() || job !== null} onClick={() => void submit()}>{busy ? "处理中…" : "授权并导入内容"}</button></p>
      {job && <>
        <p>导入任务：{job.id} · {job.status} {job.error_code}</p>
        <button className="button secondary" disabled={busy} onClick={() => void refresh()}>刷新导入状态与快照</button>
        {["succeeded", "failed", "cancelled"].includes(job.status) && <p><button className="button secondary" disabled={busy} onClick={() => {
          setJob(null); changed(); setMessage("已准备新的导入，请编辑内容并重新确认授权；已有快照保留。");
        }}>准备新的人工导入</button></p>}
      </>}
      <p role="status" aria-label="正文导入状态">{message}</p>
    </section>
  </details>;
}
