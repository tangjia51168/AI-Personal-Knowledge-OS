"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useId, useState } from "react";
import { Link2, Globe2 } from "lucide-react";
import { SourceCollection } from "./knowledge";
import { boundedFilters, contentPage, ContentPagination, PlatformOptions, platformNames } from "./content-list";

type SourceFilters = { q: string; platform: string; status: string };
const emptyFilters: SourceFilters = { q: "", platform: "", status: "" };

type Candidate = {
  original_url: string;
  canonical_url: string | null;
  accepted: boolean;
  reason: string | null;
};

type InspectResult = { candidates: Candidate[]; truncated: boolean };
type CheckedInput = { text: string; idempotencyKey: string; result: InspectResult };
type SavedSource = {
  id: string;
  batch_id: string;
  original_url: string;
  canonical_url: string | null;
  status: "accepted" | "rejected";
  rejection_code: string | null;
  created_at: string;
};

function boundedPayload(text: string, idempotencyKey?: string) {
  if (new TextEncoder().encode(text).length > 16 * 1024) throw new Error("分享文本超过 16KiB UTF-8 上限，请缩短后重试。");
  const body = JSON.stringify(idempotencyKey ? { text, idempotency_key: idempotencyKey } : { text });
  if (new TextEncoder().encode(body).length > 17 * 1024) throw new Error("请求超过 17KiB 上限，请减少文本或转义字符。");
  return body;
}

export default function Inbox({ token, space, canSummarize = false }: { token: string; space: string; canSummarize?: boolean }) {
  const filterId = useId();
  const [text, setText] = useState("");
  const [checked, setChecked] = useState<CheckedInput | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<"inspect" | "save" | "load" | null>(null);
  const [saved, setSaved] = useState(false);
  const [sources, setSources] = useState<SavedSource[] | null>(null);
  const [listMessage, setListMessage] = useState("尚未加载已保存候选。");
  const [filters, setFilters] = useState(emptyFilters);
  const [applied, setApplied] = useState(emptyFilters);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const result = checked?.result;

  function changeText(value: string) {
    setText(value);
    setChecked(null);
    setSaved(false);
    setMessage("");
  }

  async function inspect() {
    setBusy("inspect");
    setMessage("");
    setChecked(null);
    setSaved(false);
    try {
      const idempotencyKey = crypto.randomUUID();
      // Include the later save wrapper in the same bound before showing a result.
      boundedPayload(text, idempotencyKey);
      const response = await fetch(`/api/account/spaces/${space}/sources/inspect`, {
        method: "POST",
        cache: "no-store",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: boundedPayload(text),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.code ?? "REQUEST_FAILED");
      setChecked({ text, idempotencyKey, result: data.data });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "请求失败");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!checked || checked.result.truncated || checked.result.candidates.length === 0 || saved) return;
    setBusy("save");
    setMessage("");
    try {
      const response = await fetch(`/api/account/spaces/${space}/sources`, {
        method: "POST",
        cache: "no-store",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        // Keep the inspected text and key after an uncertain response so retry
        // returns the committed batch rather than creating another one.
        body: boundedPayload(checked.text, checked.idempotencyKey),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.code ?? "REQUEST_FAILED");
      setSaved(true);
      setMessage(`已保存批次 ${data.data.batch_id}：接受 ${data.data.accepted_count}，拒绝 ${data.data.rejected_count}。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(null);
    }
  }

  async function load(nextFilters = applied, pages: (string | null)[] = [null]) {
    setBusy("load");
    try {
      const normalized = boundedFilters(nextFilters);
      const data = await contentPage<SavedSource>(token, space, "sources", normalized, pages[pages.length - 1]);
      setSources(data.data); setApplied(normalized); setCursors(pages); setNextCursor(data.next_cursor);
      setListMessage(`已加载第 ${pages.length} 页，共 ${data.data.length} 条已保存候选。`);
    } catch (error) {
      setListMessage(error instanceof Error ? error.message : "已保存候选加载失败");
    } finally {
      setBusy(null);
    }
  }

  return <section aria-label="内容收件箱">
    <h2>内容收件箱</h2>
    <p>粘贴分享文本后检查链接，再保存本批候选。保存只记录 URL 与拒绝原因，尚未访问平台或采集内容。</p>
    <p>每批最多 20 个候选；文本上限 16KiB UTF-8，含转义字符的整个请求最多 17KiB。</p>
    <textarea
      aria-label="分享文本"
      value={text}
      onChange={event => changeText(event.target.value)}
      disabled={busy !== null}
      maxLength={16 * 1024}
      rows={7}
      placeholder="粘贴包含链接的分享文本"
      style={{ display: "block", width: "100%", padding: 10, marginBlock: 12 }}
    />
    <button className="button primary" disabled={busy !== null || !text.trim()} onClick={() => void inspect()}>
      {busy === "inspect" ? "检查中…" : "检查链接"}
    </button>
    {result && result.candidates.length > 0 && <button className="button secondary" disabled={busy !== null || saved || result.truncated} onClick={() => void save()}>{busy === "save" ? "保存中…" : "保存本批候选"}</button>}
    <p role="status" aria-label="来源操作状态">{message}</p>
    {result && <div aria-live="polite">
      <p>发现 {result.candidates.length} 个候选{result.truncated ? "（超过上限，仅展示前 20 个；请减少链接后重新检查）" : ""}。</p>
      {result.candidates.length === 0 ? <p>未发现可识别的 URL。</p> : <ul>
        {result.candidates.map((candidate, index) => <li key={`${candidate.original_url}-${index}`} style={{ marginBlock: 12, overflowWrap: "anywhere" }}>
          <strong>{candidate.accepted ? "可保存候选" : "已拦截"}</strong><br />
          原始：{candidate.original_url}<br />
          规范：{candidate.canonical_url ?? "—"}
          {!candidate.accepted && <><br />原因：{candidate.reason}</>}
        </li>)}
      </ul>}
    </div>}
    <section aria-label="已保存来源候选">
      <h3>已保存来源候选</h3>
      <p>按保存时间从新到旧分页。接受表示 URL 候选已入库；重复链接会记录固定拒绝原因。翻页期间内容可能发生变化，刷新会回到首页。</p>
      <p>关键词按原始及规范 URL 查找；来源类型按规范 URL 识别，不代表采集能力。被拒绝候选没有来源类型。</p>
      <form aria-label="来源筛选" onSubmit={event => { event.preventDefault(); void load(filters); }}>
        <label htmlFor={`${filterId}-q`}>来源关键词</label><input id={`${filterId}-q`} value={filters.q} disabled={busy !== null} onChange={event => setFilters({ ...filters, q: event.target.value })} />{" "}
        <label htmlFor={`${filterId}-platform`}>来源类型</label><select id={`${filterId}-platform`} value={filters.platform} disabled={busy !== null} onChange={event => setFilters({ ...filters, platform: event.target.value })}><PlatformOptions /></select>{" "}
        <label htmlFor={`${filterId}-status`}>候选状态</label><select id={`${filterId}-status`} value={filters.status} disabled={busy !== null} onChange={event => setFilters({ ...filters, status: event.target.value })}><option value="">全部状态</option><option value="accepted">已接受</option><option value="rejected">已拒绝</option></select>{" "}
        <button className="button secondary" disabled={busy !== null} type="submit">应用来源筛选</button>{" "}
        <button className="button secondary" disabled={busy !== null} type="button" onClick={() => { setFilters(emptyFilters); void load(emptyFilters); }}>重置来源筛选</button>
      </form>
      {sources && <p aria-label="当前来源筛选">当前筛选：关键词 {applied.q || "全部"} · {platformNames[applied.platform] || "全部来源类型"} · {applied.status === "accepted" ? "已接受" : applied.status === "rejected" ? "已拒绝" : "全部状态"}</p>}
      <button className="button secondary" disabled={busy !== null} onClick={() => void load()}>{busy === "load" ? "加载中…" : "刷新已保存候选"}</button>
      <p role="status" aria-label="来源列表状态">{listMessage}</p>
      {sources && <ContentPagination label="来源" page={cursors.length} nextCursor={nextCursor} disabled={busy !== null} onFirst={() => void load()} onPrevious={() => void load(applied, cursors.slice(0, -1))} onNext={() => { if (nextCursor) void load(applied, [...cursors, nextCursor]); }} />}
      {sources && (sources.length === 0 ? <p>当前筛选下暂无已保存候选。</p> : <ul className="source-card-grid" aria-label="来源结果">
        {sources.map(source => <li key={source.id} className="source-library-card">
          <div className="source-card-cover" aria-hidden="true"><Globe2 size={30} /><Link2 size={18} /></div>
          <strong>{source.status === "accepted" ? "URL 候选已入库" : "已拒绝"}</strong><br />
          原始：{source.original_url}<br />规范：{source.canonical_url ?? "—"}<br />
          {source.rejection_code && <>原因：{source.rejection_code}<br /></>}
          批次：{source.batch_id}<br />
          <time dateTime={source.created_at}>{new Date(source.created_at).toLocaleString()}</time>
          {source.status === "accepted" && <SourceCollection token={token} space={space} sourceId={source.id} canSummarize={canSummarize} />}
        </li>)}
      </ul>)}
    </section>
  </section>;
}
