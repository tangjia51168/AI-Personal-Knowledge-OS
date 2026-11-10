"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useState } from "react";

type Asset = { id: string; filename: string; mime_type: string; size: number; status: string };

export default function Files({ token, space }: { token: string; space: string }) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const path = `/api/account/spaces/${space}/assets`;

  async function request(suffix: string, method = "GET", body?: File) {
    const response = await fetch(`${path}${suffix}`, { method, cache: "no-store", headers: {
      Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": body.type || "application/octet-stream" } : {}),
    }, body });
    if (!response.ok) { const data = await response.json(); throw new Error(data.error?.code ?? "REQUEST_FAILED"); }
    return response;
  }
  async function refresh() { setAssets((await (await request("")).json()).data); }
  async function action(run: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await run(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "请求失败"); }
    finally { setBusy(false); }
  }
  async function download(asset: Asset) {
    const ticket = (await (await request(`/${asset.id}/download-ticket`, "POST")).json()).data;
    const query = new URLSearchParams({ expires: String(ticket.expires), signature: ticket.signature });
    const blob = await (await request(`/${asset.id}/download?${query}`)).blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = asset.filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section aria-label="租户文件">
    <h2>空间文件</h2>
    <p>私有存储，仅有效成员可下载。单文件 10MiB，空间 100MiB/1000 个文件；支持 UTF-8 文本、PDF、PNG、JPEG。不进行 AI 分析或文件解析。</p>
    <label>选择文件<input type="file" accept="text/plain,application/pdf,image/png,image/jpeg" disabled={busy} onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>{" "}
    <button disabled={busy || !file} onClick={() => void action(async () => {
      if (!file) return;
      if (file.size > 10 * 1024 * 1024) throw new Error("FILE_SIZE_LIMIT");
      await request(`?filename=${encodeURIComponent(file.name)}`, "POST", file);
      await refresh(); setMessage("文件已写入私有存储。");
    })}>上传文件</button>{" "}
    <button disabled={busy} onClick={() => void action(refresh)}>刷新文件</button>
    <p role="status">{message}</p>
    <ul>{assets.map(asset => <li key={asset.id} style={{ marginBlock: 16, overflowWrap: "anywhere" }}>
      {asset.filename} · {asset.size} bytes · {asset.status}{" "}
      {asset.status === "ready" && <button disabled={busy} onClick={() => void action(() => download(asset))}>下载 {asset.filename}</button>}{" "}
      <button disabled={busy} onClick={() => void action(async () => { if (!confirm(`删除 ${asset.filename}？`)) return; await request(`/${asset.id}`, "DELETE"); await refresh(); })}>删除 {asset.filename}</button>
    </li>)}</ul>
    <p>中断上传保留 pending 记录并计入配额，15 分钟后可删除清理；删除失败保留 deleting 状态供重试。下载票据 60 秒有效，仍需身份与成员验证。尚未完成恶意文件扫描，不可公开上线。</p>
  </section>;
}
