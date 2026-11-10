"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useState } from "react";

type Job = { id: string; kind: string; status: string; progress: number; attempt_count: number; error_code: string | null };
const kinds: Record<string, string> = { space_export: "空间元数据导出", source_collect: "公开内容采集", source_import: "授权正文／字幕导入" };

export default function Jobs({ token, space }: { token: string; space: string }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  // Retain the key across a failed request; retrying a lost response cannot submit twice.
  const [key, setKey] = useState(() => crypto.randomUUID());

  async function request(suffix: string, method = "GET", body?: object) {
    const response = await fetch(`/api/account/spaces/${space}/jobs${suffix}`, {
      method, cache: "no-store", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.code ?? "REQUEST_FAILED");
    return data.data;
  }

  async function action(run: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await run(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "请求失败"); }
    finally { setBusy(false); }
  }

  async function refresh() { setJobs(await request("")); }

  return <section aria-label="持久化任务中心">
    <h2>任务中心</h2>
    <p>可创建真实空间元数据导出（不包含文件内容、其他成员身份或 AI）。状态来自数据库；手动刷新查看执行结果。</p>
    <button className="button primary" disabled={busy} onClick={() => void action(async () => {
      await request("", "POST", { kind: "space_export", idempotency_key: key });
      setKey(crypto.randomUUID()); await refresh(); setMessage("任务已持久化，等待独立 Worker 执行。");
    })}>创建空间元数据导出</button>{" "}
    <button className="button secondary" disabled={busy} onClick={() => void action(refresh)}>刷新任务</button>
    <p role="status">{message}</p>
    {jobs.length === 0 ? <p>尚未加载任务，或当前用户没有任务。</p> : <ul>{jobs.map(job => <li key={job.id} style={{ marginBlock: 16, overflowWrap: "anywhere" }}>
      <p>{kinds[job.kind] ?? job.kind} · {job.id} · {job.status} · {job.progress}% · 执行 {job.attempt_count} 次 {job.error_code}</p>
      {["queued", "running"].includes(job.status) && <button disabled={busy} onClick={() => void action(async () => { await request(`/${job.id}/cancel`, "POST"); await refresh(); })}>取消</button>}
      {job.status === "failed" && job.attempt_count < 3 && <button disabled={busy} onClick={() => void action(async () => { await request(`/${job.id}/retry`, "POST"); await refresh(); })}>重试</button>}
      {job.status === "succeeded" && <button disabled={busy} onClick={() => void action(async () => { setResult(JSON.stringify(await request(`/${job.id}/result`), null, 2)); })}>{job.kind === "space_export" ? "查看导出结果" : "查看任务结果"}</button>}
    </li>)}</ul>}
    {result && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{result}</pre>}
    <p>每空间最多 10 个待处理任务、每日 100 个任务；每任务最多执行 3 次。不自动采集平台内容或调用付费模型。</p>
  </section>;
}
