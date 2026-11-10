"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useRef, useState } from "react";
import { readingStateNames, readingStates } from "@/lib/learning-summary";
import NoteEditor from "./note-editor";
import NoteAnnotations from "./note-annotations";
import NoteConcepts from "./note-concepts";
import NoteLabels, { KnowledgeLabel, labelKindNames, loadLabelCatalog } from "./note-labels";
import LabelManager from "./label-manager";
import NoteLinks from "./note-links";
import RevisionView, { type SavedRevision } from "./revision-view";
import KnowledgeExplorer from "./knowledge-explorer";
import Learning from "./learning";
import BatchExport from "./batch-export";
import ReminderBatch, { type ReminderNote } from "./reminder-batch";
import SourceImport from "./source-import";
import ManualNote from "./manual-note";
import { boundedFilters, contentPage, ContentPagination, PlatformOptions, platformNames } from "./content-list";

type Access = { token: string; space: string };
type Snapshot = {
  id: string; source_id: string; version: number; title: string; author: string;
  platform: string; content_kind: string; limitations: string[]; created_at: string;
  provenance: "platform_collection" | "user_import";
  body_text?: string; fetched_url?: string; details?: Record<string, unknown>;
};
type Run = { run_id: string; status: "pending" | "succeeded" | "failed" | "unknown"; error_code: string | null; note_id: string | null; revision_id: string | null; completed_stages?: number; can_resume?: boolean };
type Origin = "ai_generated" | "user_edit";
type NoteFilters = { q: string; platform: string; origin: string; label_id: string; reading_status: string; review: string };
const emptyNoteFilters: NoteFilters = { q: "", platform: "", origin: "", label_id: "", reading_status: "", review: "" };
type Revision = SavedRevision;
type Note = { id: string; source_id: string; title: string; version: number; origin: Origin; created_at: string; reading_status: keyof typeof readingStateNames; learning_recorded: boolean; reminder_at: string | null };
type NoteDetail = { id: string; source_id: string; revision: Revision | null; revisions: { id: string; version: number; title: string; origin: Origin; created_at: string }[] };
type Capabilities = { enabled: boolean; platforms: { platform: string; metadata: boolean; article_text: boolean; transcript: boolean; comments: boolean; danmaku: boolean }[] };
type Job = { id: string; status: string; error_code: string | null };

async function request<T>(access: Access, path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/account/spaces/${access.space}/${path}`, {
    method: body ? "POST" : "GET", cache: "no-store",
    headers: { Authorization: `Bearer ${access.token}`, "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.code ?? "REQUEST_FAILED");
  return result.data as T;
}

function Summary({ token, space, snapshot, canSummarize }: Access & { snapshot: Snapshot; canSummarize: boolean }) {
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[] | null>(null);
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState<Revision | null>(null);

  async function showResult(result: Run) {
    setRun(result);
    if (result.status === "succeeded" && result.note_id && result.revision_id) {
      const note = await request<NoteDetail>({ token, space }, `notes/${result.note_id}`);
      setRevision(note.revision?.id === result.revision_id ? note.revision : await request<Revision>({ token, space }, `notes/${result.note_id}/revisions/${result.revision_id}`));
    }
  }

  async function act(queryOnly: boolean, resume = false) {
    setBusy(true); setMessage("");
    try {
      const result = resume && run
        ? await request<Run>({ token, space }, `note-runs/${run.run_id}/resume`, {consent:true})
        : queryOnly && run
        ? await request<Run>({ token, space }, `note-runs/${run.run_id}`)
        : await request<Run>({ token, space }, `snapshots/${snapshot.id}/summarize`, { consent: true, idempotency_key: key });
      await showResult(result);
      setMessage(result.status === "succeeded" ? "结构化笔记已保存。" : result.status === "pending" ? "生成结果尚未确认，请查询状态；不会自动再次付费。" : result.status === "unknown" ? "模型结果未知，禁止自动重试。请保留此记录并核查用量。" : `未生成笔记：${result.error_code ?? "SUMMARY_FAILED"}`);
    } catch (error) {
      setMessage(`${error instanceof Error ? error.message : "请求失败"}。重试将使用原幂等键，不新建生成请求。`);
    } finally { setBusy(false); }
  }

  return <section aria-label="快照 AI 总结">
    <h4>生成可追溯笔记</h4>
    {snapshot.content_kind === "metadata" && <p>此快照仅有元数据/简介，没有视频正文；生成结果仅能描述这些信息，不能作为视频完整总结。</p>}
    {snapshot.provenance === "user_import" && <p>此快照由用户人工提供，尚未核验与关联平台内容是否一致；笔记只能依据导入内容。</p>}
    <p>内容将发送至已授权的总结供应商，可能产生费用。当前支持 DeepSeek；短文一次请求，长文逐段分析后综合，最多 8 段 + 1 次综合，每次最多 2048 输出 token；任一步失败停止，不自动重试。最多处理 60000 字符；分段覆盖不代表事实核验或语义无遗漏。</p>
    {canSummarize ? <>
      <p><button className="button secondary" disabled={busy} onClick={() => { setBusy(true); setMessage(""); void request<Run[]>({token,space},`snapshots/${snapshot.id}/note-runs`).then(setHistory).catch(() => {setHistory(null);setMessage("生成记录读取失败，请手动重试。");}).finally(() => setBusy(false)); }}>读取此快照的生成记录</button></p>
      {history && <ul aria-label="快照生成记录">{history.map(item => <li key={item.run_id}>{item.status} · 已保存 {item.completed_stages ?? 0} 阶段 <button className="button secondary" disabled={busy} onClick={() => {setRevision(null);setBusy(true);void showResult(item).catch(() => {setRevision(null);setMessage("生成结果读取失败，请重新查询状态。");}).finally(() => setBusy(false));}}>选择此生成记录</button></li>)}</ul>}
      <label><input type="checkbox" checked={consent} disabled={busy || run !== null} onChange={event => setConsent(event.target.checked)} />我同意发送这份快照内容并承担本次模型费用</label>
      <p><button className="button primary" disabled={busy || !consent || run !== null} onClick={() => void act(false)}>{busy ? "处理中…" : "授权并生成笔记"}</button>{" "}
        {run && <button className="button secondary" disabled={busy} onClick={() => void act(true)}>查询生成状态</button>}</p>
      {run?.status === "failed" && <p><button className="button secondary" disabled={busy} onClick={() => { setKey(crypto.randomUUID()); setConsent(false); setRun(null); setRevision(null); setMessage("已准备新的生成请求，请重新确认费用与内容发送授权。"); }}>重新授权新的生成请求</button></p>}
      {run?.can_resume && <p>已保存 {run.completed_stages} 个成功阶段；继续只执行尚未发出的阶段，可能产生费用。<button className="button secondary" disabled={busy} onClick={() => void act(false, true)}>授权继续未发出阶段</button></p>}
    </> : <p>需空间管理员授权生成；成员可以查看已生成笔记。</p>}
    {run && <p>记录：{run.run_id} · {run.status} {run.error_code}</p>}
    <p role="status" aria-label="总结状态">{message}</p>
    {revision && run?.note_id && <RevisionView revision={revision} token={token} space={space} noteId={run.note_id} />}
  </section>;
}

export function SourceCollection({ token, space, sourceId, canSummarize }: Access & { sourceId: string; canSummarize: boolean }) {
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[] | null>(null);
  const [selected, setSelected] = useState<Snapshot | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [message, setMessage] = useState("");

  async function act(action: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await action(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "请求失败"); }
    finally { setBusy(false); }
  }

  async function refresh() {
    const access = { token, space };
    const [caps, versions] = await Promise.all([
      request<Capabilities>(access, "sources/capabilities"),
      request<Snapshot[]>(access, `sources/${sourceId}/snapshots`),
    ]);
    setCapabilities(caps); setSnapshots(versions);
    if (job) setJob(await request<Job>(access, `jobs/${job.id}`));
    setMessage(`已加载 ${versions.length} 份真实快照（最多显示最近 20 份）。`);
  }

  return <section aria-label={`来源采集 ${sourceId}`}>
    <SourceImport key={`${space}-${sourceId}`} token={token} space={space} sourceId={sourceId} onChanged={refresh} />
    <p><button className="button secondary" disabled={busy} onClick={() => void act(refresh)}>查看采集能力与快照</button></p>
    {capabilities && <>
      <p>采集开关：{capabilities.enabled ? "已启用" : "运维尚未启用"}。网页支持正文提取，B站/YouTube 当前只支持公开元数据；字幕、评论、弹幕和合集尚未接入。</p>
      <p>能力按每次真实请求确认，识别平台不代表可以访问其全部内容。</p>
      <label><input type="checkbox" checked={consent} disabled={busy || job !== null} onChange={event => setConsent(event.target.checked)} />我授权访问该公开链接并保存内容快照</label>
      <p><button className="button primary" disabled={busy || !consent || !capabilities.enabled || job !== null} onClick={() => void act(async () => {
        const result = await request<Job>({ token, space }, `sources/${sourceId}/collect`, { consent: true, idempotency_key: key });
        setJob(result); setMessage("采集任务已保存，独立 Worker 将执行。请手动刷新快照与任务状态。");
      })}>采集公开内容</button></p>
      {job && <p>任务：{job.id} · {job.status} {job.error_code}。可在任务中心查看或重试失败任务。</p>}
      {job?.status === "succeeded" && <p><button className="button secondary" disabled={busy} onClick={() => { setKey(crypto.randomUUID()); setJob(null); setConsent(false); setMessage("已准备新版本采集，请重新授权；已有快照保留。"); }}>准备采集新版本</button></p>}
      <button className="button secondary" disabled={busy} onClick={() => void act(refresh)}>刷新快照与采集状态</button>
      {snapshots && (snapshots.length === 0 ? <p>暂无内容快照。</p> : <ul>{snapshots.map(snapshot => <li key={snapshot.id}>
        <p>{snapshot.title} · 版本 {snapshot.version} · {snapshot.platform} · {snapshot.content_kind} · {snapshot.provenance === "user_import" ? "人工导入（未核验）" : "公开平台采集"}</p>
        <button disabled={busy} onClick={() => void act(async () => { setSelected(await request<Snapshot>({ token, space }, `sources/${sourceId}/snapshots/${snapshot.id}`)); })}>打开快照版本 {snapshot.version}</button>
      </li>)}</ul>)}
    </>}
    <p role="status" aria-label="采集状态">{message}</p>
    {selected && <section aria-label="来源快照">
      <h4>{selected.title} · 快照版本 {selected.version}</h4>
      <p>{selected.author || "作者未提供"} · {selected.platform} · {selected.content_kind}</p>
      {selected.provenance === "user_import" ? <p>人工导入（未核验）。关联来源地址：{selected.fetched_url}；导入时未访问该地址。</p> : <p>访问地址：{selected.fetched_url}</p>}
      {selected.limitations.length > 0 && <ul>{selected.limitations.map((value, index) => <li key={index}>{value}</li>)}</ul>}
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{selected.body_text}</pre>
      <ManualNote key={`${space}-${selected.id}-${token}`} token={token} space={space} snapshot={selected} />
      <Summary key={selected.id} token={token} space={space} snapshot={selected} canSummarize={canSummarize} />
    </section>}
  </section>;
}

export default function Knowledge({ token, space }: Access) {
  const filterId = useId();
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [detail, setDetail] = useState<NoteDetail | null>(null);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [editing, setEditing] = useState<Revision | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("尚未加载知识笔记。");
  const [labels, setLabels] = useState<KnowledgeLabel[]>([]);
  const [labelBusy, setLabelBusy] = useState(false);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [labelGeneration, setLabelGeneration] = useState(0);
  const [annotationLocked, setAnnotationLocked] = useState(false);
  const [conceptLocked, setConceptLocked] = useState(false);
  const [linkBusy, setLinkBusy] = useState(false);
  const [reminderBusy, setReminderBusy] = useState(false);
  const [reminderSelection, setReminderSelection] = useState<ReminderNote[]>([]);
  const [filters, setFilters] = useState(emptyNoteFilters);
  const [applied, setApplied] = useState(emptyNoteFilters);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const listRequest = useRef<AbortController | null>(null);
  useEffect(() => () => listRequest.current?.abort(), []);
  const listDisabled = busy || labelBusy || catalogBusy || linkBusy || reminderBusy || annotationLocked || conceptLocked || editing !== null;

  function invalidateLearningList() {
    listRequest.current?.abort(); setNotes(null); setNextCursor(null); setCursors([null]);
    setMessage("学习状态已更新，请刷新知识笔记重新读取清单；已打开的笔记保留。");
  }

  async function load(nextFilters = applied, pages: (string | null)[] = [null]) {
    const normalized = boundedFilters(nextFilters);
    listRequest.current?.abort();
    const controller = new AbortController();
    listRequest.current = controller;
    setNotes(null); setNextCursor(null);
    try {
      const [result, catalog] = await Promise.all([contentPage<Note>(token, space, "notes", normalized, pages[pages.length - 1], controller.signal), loadLabelCatalog({ token, space })]);
      if (controller.signal.aborted) return;
      setLabels(catalog);
      setNotes(result.data); setApplied(normalized); setCursors(pages); setNextCursor(result.next_cursor);
      setMessage(`已加载真实知识笔记，第 ${pages.length} 页共 ${result.data.length} 条。`);
    } catch (error) {
      if (!controller.signal.aborted) throw new Error(`${error instanceof Error ? error.message : "读取失败"}；未展示旧清单，请手动刷新；已打开的内容保留。`);
    } finally {
      if (listRequest.current === controller) listRequest.current = null;
    }
  }

  async function act(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "请求失败"); }
    finally { setBusy(false); }
  }

  return <section aria-label="知识笔记">
    <h2>知识笔记</h2>
    <p>笔记保留结构化章节、Markdown、来源证据与版本历史。可人工修订、追加个人理解，并用主题和标签组织笔记；本地语义检索需先为固定修订建立索引。</p>
    <KnowledgeExplorer key={`explorer-${space}-${token}`} token={token} space={space} disabled={listDisabled} labels={labels} />
    <LabelManager key={`catalog-${space}-${token}`} token={token} space={space} disabled={busy || labelBusy || linkBusy || reminderBusy || annotationLocked || conceptLocked || editing !== null} onBusy={setCatalogBusy} onCatalog={setLabels} onSaved={() => {
      listRequest.current?.abort(); setNotes(null); setNextCursor(null); setCursors([null]); setLabels([]); setLabelGeneration(value => value + 1);
      setMessage("分类目录已更新，请刷新知识笔记重新读取名称与筛选；已打开的笔记保留。");
    }} />
    <p>关键词查找最新版本的标题与 Markdown 正文；来源类型按关联 URL 识别。按笔记创建时间从新到旧分页，刷新回到首页，翻页期间最新版本可能变化。</p>
    <section className="learning-review-list" aria-label="个人学习筛选与复习清单">
    <h3>个人学习筛选与复习清单</h3>
    <p>按自己的状态和提醒时间查找笔记；未保存记录计为未看。清单按笔记创建时间排列，状态变化后请刷新。到期与未来提醒均排除细看；提醒时间按本机时区显示。</p>
    <form aria-label="笔记筛选" onSubmit={event => { event.preventDefault(); if (!listDisabled) void act(() => load(filters)); }}>
      <label htmlFor={`${filterId}-q`}>笔记关键词</label><input id={`${filterId}-q`} value={filters.q} disabled={listDisabled} onChange={event => setFilters({ ...filters, q: event.target.value })} />{" "}
      <label htmlFor={`${filterId}-platform`}>笔记来源类型</label><select id={`${filterId}-platform`} value={filters.platform} disabled={listDisabled} onChange={event => setFilters({ ...filters, platform: event.target.value })}><PlatformOptions /></select>{" "}
      <label htmlFor={`${filterId}-origin`}>最新版本来源</label><select id={`${filterId}-origin`} value={filters.origin} disabled={listDisabled} onChange={event => setFilters({ ...filters, origin: event.target.value })}><option value="">全部版本来源</option><option value="ai_generated">模型生成</option><option value="user_edit">人工修订</option></select>{" "}
      <label htmlFor={`${filterId}-label`}>笔记主题或标签</label><select id={`${filterId}-label`} value={filters.label_id} disabled={listDisabled} onChange={event => setFilters({ ...filters, label_id: event.target.value })}><option value="">全部分类</option>{labels.map(label => <option key={label.id} value={label.id}>{labelKindNames[label.kind]}：{label.name}</option>)}</select>{" "}
      <label htmlFor={`${filterId}-reading`}>个人阅读状态</label><select id={`${filterId}-reading`} value={filters.reading_status} disabled={listDisabled} onChange={event => setFilters({ ...filters, reading_status: event.target.value })}><option value="">全部阅读状态</option>{readingStates.map(state => <option key={state} value={state}>{readingStateNames[state]}</option>)}<option value="unrecorded">尚无个人学习记录</option></select>{" "}
      <label htmlFor={`${filterId}-review`}>复习提醒筛选</label><select id={`${filterId}-review`} value={filters.review} disabled={listDisabled} onChange={event => setFilters({ ...filters, review: event.target.value })}><option value="">全部提醒范围</option><option value="due">已到期，尚未细看</option><option value="upcoming">未来提醒，尚未细看</option></select>{" "}
      <button className="button secondary" disabled={listDisabled} type="submit">应用笔记筛选</button>{" "}
      <button className="button secondary" disabled={listDisabled} type="button" onClick={() => { setFilters(emptyNoteFilters); void act(() => load(emptyNoteFilters)); }}>重置笔记筛选</button>
    </form>
    {notes && <p aria-label="当前笔记筛选">当前筛选：关键词 {applied.q || "全部"} · {platformNames[applied.platform] || "全部来源类型"} · {applied.origin === "ai_generated" ? "模型生成" : applied.origin === "user_edit" ? "人工修订" : "全部版本来源"} · {applied.label_id ? labels.find(label => label.id === applied.label_id)?.name ?? "已选分类" : "全部分类"} · {applied.reading_status === "unrecorded" ? "尚无个人学习记录" : readingStateNames[applied.reading_status as keyof typeof readingStateNames] || "全部阅读状态"} · {applied.review === "due" ? "已到期，尚未细看" : applied.review === "upcoming" ? "未来提醒，尚未细看" : "全部提醒范围"}</p>}
    <button className="button secondary" disabled={listDisabled} onClick={() => void act(() => load())}>刷新知识笔记</button>
    {editing && <p>修订期间暂停筛选与翻页；请先保存或明确放弃草稿。</p>}
    <p role="status" aria-label="笔记列表状态">{message}</p>
    <BatchExport key={`batch-${space}-${token}`} token={token} space={space} current={detail && revision ? { note_id: detail.id, revision_id: revision.id, title: revision.title } : null} />
    <ReminderBatch key={`reminders-${space}-${token}-${reminderSelection.map(note => note.id).join("-")}`} token={token} space={space} selection={reminderSelection} disabled={busy || labelBusy || linkBusy || annotationLocked || conceptLocked || editing !== null} onBusy={setReminderBusy} onClear={() => setReminderSelection([])} onChanged={invalidateLearningList} />
    {notes && <ContentPagination label="笔记" page={cursors.length} nextCursor={nextCursor} disabled={listDisabled} onFirst={() => void act(() => load())} onPrevious={() => void act(() => load(applied, cursors.slice(0, -1)))} onNext={() => { if (nextCursor) void act(() => load(applied, [...cursors, nextCursor])); }} />}
    {notes && (notes.length === 0 ? <p>当前筛选下暂无笔记。可重置筛选，或在收件箱保存快照并授权总结。</p> : <ul aria-label="笔记结果">{notes.map(note => <li key={note.id}>
      <label><input type="checkbox" checked={reminderSelection.some(item => item.id === note.id)} disabled={listDisabled || (reminderSelection.length >= 20 && !reminderSelection.some(item => item.id === note.id))} onChange={event => setReminderSelection(event.target.checked ? [...reminderSelection, { id: note.id, title: note.title }] : reminderSelection.filter(item => item.id !== note.id))} />选择提醒：{note.title}</label>
      <p>{note.title} · 版本 {note.version} · {note.origin === "user_edit" ? "人工修订" : "模型生成"}</p>
      <p>个人阅读：{readingStateNames[note.reading_status]}{!note.learning_recorded && "（尚无个人学习记录）"}{note.reminder_at && ` · 提醒 ${new Date(note.reminder_at).toLocaleString()}（本机时区）`}</p>
      <button disabled={listDisabled} onClick={() => void act(async () => { const result = await request<NoteDetail>({ token, space }, `notes/${note.id}`); setDetail(result); setRevision(result.revision); })}>打开笔记</button>
    </li>)}</ul>)}
    </section>
    {detail && <p>已打开的笔记独立保留，可能不在当前筛选页内。版本列表显示最近 20 个版本。<label>笔记版本<select aria-label="笔记版本" value={revision?.id ?? ""} disabled={listDisabled} onChange={event => void act(async () => setRevision(await request<Revision>({ token, space }, `notes/${detail.id}/revisions/${event.target.value}`)))}>
      {detail.revisions.map(item => <option key={item.id} value={item.id}>版本 {item.version} · {item.title} · {item.origin === "user_edit" ? "人工修订" : "模型生成"}</option>)}
    </select></label></p>}
    {revision && detail && <RevisionView revision={revision} token={token} space={space} noteId={detail.id} />}
    {detail && revision && <NoteConcepts key={`concepts-${space}-${token}-${detail.id}-${revision.id}`} token={token} space={space} noteId={detail.id} revision={revision} disabled={busy || labelBusy || catalogBusy || linkBusy || reminderBusy || annotationLocked || editing !== null} onLockChange={setConceptLocked} />}
    {detail && revision && <NoteAnnotations key={`annotations-${space}-${detail.id}-${revision.id}`} token={token} space={space} noteId={detail.id} revision={revision} disabled={busy || labelBusy || linkBusy || conceptLocked || editing !== null} onLockChange={setAnnotationLocked} />}
    {detail && <Learning key={`learning-${space}-${detail.id}`} token={token} space={space} noteId={detail.id} onSaved={invalidateLearningList} />}
    {detail && <NoteLinks key={`links-${space}-${detail.id}`} token={token} space={space} noteId={detail.id} disabled={busy || labelBusy || annotationLocked || conceptLocked || editing !== null} onBusyChange={setLinkBusy} onOpen={async noteId => {
      const result = await request<NoteDetail>({ token, space }, `notes/${noteId}`);
      setDetail(result); setRevision(result.revision); setEditing(null);
      setMessage("已从双向链接打开关联笔记。");
    }} />}
    {detail && <NoteLabels key={`labels-${space}-${detail.id}-${labelGeneration}`} token={token} space={space} noteId={detail.id} disabled={busy || catalogBusy || linkBusy || annotationLocked || conceptLocked || editing !== null} onCatalogChanged={setLabels} onBusyChange={setLabelBusy} onChanged={async () => {
      await load();
      setMessage("笔记分类已更新，已按原筛选刷新首页；已打开的详情仍保留。");
    }} />}
    {detail && revision && !editing && (detail.revision?.id === revision.id
      ? <p><button className="button secondary" disabled={busy || labelBusy || linkBusy || annotationLocked || conceptLocked} onClick={() => setEditing(revision)}>开始人工修订</button></p>
      : <p>正在查看历史版本。<button className="button secondary" disabled={busy || labelBusy || linkBusy || annotationLocked || conceptLocked} onClick={() => void act(async () => {
        const result = await request<NoteDetail>({ token, space }, `notes/${detail.id}`);
        setDetail(result); setRevision(result.revision);
      })}>打开最新版本</button>后可开始人工修订。</p>)}
    {detail && editing && <NoteEditor key={`editor-${space}-${detail.id}-${editing.id}`} token={token} space={space} noteId={detail.id} revision={editing} onCancel={() => setEditing(null)} onSaved={async () => {
      const [list, result] = await Promise.all([contentPage<Note>(token, space, "notes", applied, null), request<NoteDetail>({ token, space }, `notes/${detail.id}`)]);
      setNotes(list.data); setCursors([null]); setNextCursor(list.next_cursor); setDetail(result); setRevision(result.revision); setEditing(null);
      setMessage(`人工修订已保存为新版本，历史版本保留。已按原筛选刷新首页。${list.data.some(note => note.id === detail.id) ? "" : "已保存笔记不在当前筛选页内，详情仍保留。"}`);
    }} onOpenLatest={async () => {
      const result = await request<NoteDetail>({ token, space }, `notes/${detail.id}`);
      setDetail(result); setRevision(result.revision); setEditing(null); setMessage("已打开最新版本，之前草稿已按你的选择放弃；可以重新开始修订。");
    }} />}
  </section>;
}
