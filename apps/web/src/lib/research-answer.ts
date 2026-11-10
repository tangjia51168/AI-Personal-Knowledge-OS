export type Citation = {
  id: string; note_id: string; revision_id: string; snapshot_id: string;
  version: number; title: string; provenance: "user_import" | "platform_collection";
  content_kind: "article" | "transcript" | "metadata"; quote: string; evidence_key: string;
};
export type ResearchRun = {
  id: string; question: string; query: string; retrieval: "literal_latest_revisions";
  status: "pending" | "succeeded" | "failed" | "unknown"; error_code: string | null;
  citations: Citation[];
  answer: { insufficient_evidence: boolean; statements: { text: string; citations: string[] }[]; limitations: string[] } | null;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function bounded(value: unknown, limit: number): value is string { return typeof value === "string" && !!value.trim() && Array.from(value).length <= limit; }
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function invalid(): never { throw new Error("INVALID_RESEARCH_RESPONSE"); }

export function parseResearchRun(value: unknown, expected?: { id?: string; question?: string; query?: string }): ResearchRun {
  if (!record(value) || typeof value.id !== "string" || !uuid.test(value.id)
    || !bounded(value.question, 1000) || !bounded(value.query, 120)
    || value.retrieval !== "literal_latest_revisions"
    || !["pending", "succeeded", "failed", "unknown"].includes(String(value.status))
    || !(value.error_code === null || typeof value.error_code === "string" && /^[A-Z0-9_]{1,64}$/.test(value.error_code))
    || !Array.isArray(value.citations) || value.citations.length > 8
    || expected?.id && value.id !== expected.id || expected?.question && value.question !== expected.question.trim()
    || expected?.query && value.query !== expected.query.trim()) invalid();
  const ids = new Set<string>();
  for (const row of value.citations) {
    if (!record(row) || typeof row.id !== "string" || !/^R00[1-8]$/.test(row.id) || ids.has(row.id)
      || ![row.note_id, row.revision_id, row.snapshot_id].every(item => typeof item === "string" && uuid.test(item))
      || !Number.isSafeInteger(row.version) || Number(row.version) < 1 || !bounded(row.title, 200)
      || !bounded(row.quote, 800) || typeof row.evidence_key !== "string" || !/^E[0-9]{3}$/.test(row.evidence_key)
      || !["user_import", "platform_collection"].includes(String(row.provenance))
      || !["article", "transcript", "metadata"].includes(String(row.content_kind))) invalid();
    ids.add(row.id);
  }
  if (value.status !== "succeeded") { if (value.answer !== null) invalid(); }
  else {
    const answer = value.answer;
    if (!record(answer) || typeof answer.insufficient_evidence !== "boolean"
      || !Array.isArray(answer.statements) || answer.statements.length > 6
      || !answer.insufficient_evidence && !answer.statements.length
      || !Array.isArray(answer.limitations) || !answer.limitations.length || answer.limitations.length > 6
      || !answer.limitations.every(item => bounded(item, 500))) invalid();
    for (const item of answer.statements) {
      if (!record(item) || !bounded(item.text, 1200) || !Array.isArray(item.citations)
        || !item.citations.length || item.citations.length > 8 || new Set(item.citations).size !== item.citations.length
        || !item.citations.every(id => typeof id === "string" && ids.has(id))) invalid();
    }
  }
  return value as ResearchRun;
}

export function parseSnapshotEvidence(value: unknown, snapshot: string): { evidence: { key: string; text: string }[]; partial: boolean } {
  if (!record(value) || value.snapshot_id !== snapshot || !Array.isArray(value.evidence)
    || value.evidence.length > 80 || !Number.isSafeInteger(value.total_segments)
    || Number(value.total_segments) < value.evidence.length || typeof value.partial !== "boolean"
    || value.partial !== (Number(value.total_segments) > value.evidence.length)) invalid();
  const keys = new Set<string>();
  for (const row of value.evidence) {
    if (!record(row) || typeof row.key !== "string" || !/^E[0-9]{3}$/.test(row.key)
      || keys.has(row.key) || !bounded(row.text, 800)) invalid();
    keys.add(row.key);
  }
  return { evidence: value.evidence, partial: value.partial };
}
