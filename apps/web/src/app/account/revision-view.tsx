"use client";

import NoteExport from "./note-export";
import NoteReader from "./note-reader";
import EvidenceRecall from "./evidence-recall";
import SemanticIndex from "./semantic-index";

export type SavedRevision = { id: string; version: number; title: string; content_md: string; snapshot_id: string; document: Record<string, unknown>; origin: "ai_generated" | "user_edit"; base_revision_id: string | null; created_by: string; created_at: string };

export default function RevisionView({ revision, token, space, noteId }: { revision: SavedRevision; token: string; space: string; noteId: string }) {
  return <section aria-label="笔记内容">
    <h4>{revision.title} · 版本 {revision.version}</h4>
    <p>{revision.origin === "user_edit" ? "人工修订；本版本未调用模型。人工文字与个人理解未经来源核验。" : "模型生成版本；请结合来源核对内容。"}{revision.base_revision_id && ` 基于修订：${revision.base_revision_id}`}</p>
    <p>来源快照：{revision.snapshot_id}。引用来自该快照片段；引用有效不代表已通过事实核验。</p>
    <NoteExport key={`${space}-${noteId}-${revision.id}`} token={token} space={space} noteId={noteId} revisionId={revision.id} />
    <NoteReader key={`reader-${space}-${noteId}-${revision.id}`} token={token} space={space} noteId={noteId} revision={revision} />
    <EvidenceRecall key={`recall-${space}-${noteId}-${revision.id}`} space={space} noteId={noteId} revision={revision} />
    <details><summary>建立本地语义检索索引</summary><SemanticIndex key={`index-${space}-${noteId}-${revision.id}`} token={token} space={space} noteId={noteId} revisionId={revision.id} /></details>
    <details><summary>查看原始 Markdown（只读）</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{revision.content_md}</pre></details>
  </section>;
}
