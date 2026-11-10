"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useRef, useState } from "react";

const formats = { markdown: ["Markdown", "md"], html: ["HTML", "html"], json: ["JSON", "json"], text: ["纯文本", "txt"], obsidian: ["Obsidian ZIP", "zip"], pptx: ["PPTX", "pptx"], image: ["图文 SVG", "svg"], flowchart: ["流程图 SVG", "svg"] } as const;
type Format = keyof typeof formats;

export default function NoteExport({ token, space, noteId, revisionId }: { token: string; space: string; noteId: string; revisionId: string }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const activeRequest = useRef<AbortController | null>(null);
  useEffect(() => () => { activeRequest.current?.abort(); }, []);
  async function download(format: Format) {
    if (activeRequest.current) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`/api/account/spaces/${space}/notes/${noteId}/revisions/${revisionId}/export?format=${format}`, {
        cache: "no-store", signal: controller.signal, headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.error?.code ?? "EXPORT_FAILED");
      }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      if (blob.size > 1_048_576) throw new Error("NOTE_EXPORT_LIMIT");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `knowledge-${revisionId}.${formats[format][1]}`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(`已准备当前修订的 ${formats[format][0]} 文件。`);
    } catch (error) { if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "导出失败"}；未生成下载文件，可手动重试。`); }
    finally { if (!controller.signal.aborted) { activeRequest.current = null; setBusy(false); } }
  }
  return <section aria-label="笔记版本导出">
    <p className="export-scope">下载当前固定修订 · 不含草稿或其他版本 · 不是完整空间备份</p>
    <details className="export-format-help"><summary>格式说明与 Obsidian 使用方法</summary>
    <p>只导出当前打开的已保存版本，不含草稿、其他版本、标签、双向链接或逐段批注。JSON、HTML、纯文本含来源与证据；Markdown 保持原版本原文。HTML 不执行脚本或加载远程资源。PPTX、图文和流程图均为本地生成的固定内容，不访问外链。不是完整备份。</p>
    <p>Obsidian ZIP：解压到新目录后，将完整目录手动放入自行选择的库并打开 note.md。E 编号可定位 evidence.md 原文；含来源元数据、校验清单和原始 Markdown。不配置插件、不访问本地库、不自动同步，尚未实测第三方 Obsidian 客户端。</p>
    </details>
    {(Object.keys(formats) as Format[]).map(format => <button key={format} className="button secondary" disabled={busy} onClick={() => void download(format)}>下载 {formats[format][0]}</button>)}
    <p role="status" aria-label="笔记导出状态">{message}</p>
  </section>;
}
