// Read-only presentation boundaries. Never interpret note text as HTML or URLs.
export const sectionNames: Record<string, string> = {
  core_claims: "核心结论与观点", steps: "操作步骤与方法", examples: "案例与场景",
  numbers: "数字与条件", takeaways: "可复用启发",
};
export type ReaderItem = { text: string; evidence_keys: string[] };
export type ReaderDocument = {
  overview: ReaderItem; sections: { kind: string; items: ReaderItem[] }[];
  limitations: string[]; personal: string | null;
  coverage: { total_segments: number; provided_segments: number; cited_segments: number; partial: boolean };
  stages?: { overview: ReaderItem; sections: { kind: string; items: ReaderItem[] }[]; limitations: string[] }[];
};
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function strings(value: unknown, count: number, length: number): value is string[] {
  return Array.isArray(value) && value.length <= count && value.every(item => typeof item === "string" && item.length <= length);
}
function item(value: unknown): ReaderItem | null {
  const row = record(value);
  if (!row || typeof row.text !== "string" || !row.text.trim() || row.text.length > 1200 || !strings(row.evidence_keys, 8, 4) || !row.evidence_keys.length || row.evidence_keys.some(key => !/^E\d{3}$/.test(key)) || new Set(row.evidence_keys).size !== row.evidence_keys.length) return null;
  return { text: row.text, evidence_keys: [...row.evidence_keys] };
}
export function readerDocument(value: unknown): ReaderDocument | null {
  const row = record(value);
  if (!row) return null;
  const overview = item(row.overview), coverage = record(row.coverage);
  if (!overview || !coverage || !Array.isArray(row.sections) || row.sections.length < 1 || row.sections.length > 5 || !strings(row.limitations, 8, 500)) return null;
  const sections: ReaderDocument["sections"] = [];
  for (const entry of row.sections) {
    const section = record(entry);
    if (!section || typeof section.kind !== "string" || !Object.hasOwn(sectionNames, section.kind) || sections.some(other => other.kind === section.kind) || !Array.isArray(section.items) || !section.items.length || section.items.length > 6) return null;
    const items = section.items.map(item);
    if (items.some(value => value === null)) return null;
    sections.push({ kind: section.kind, items: items as ReaderItem[] });
  }
  const { total_segments: total, provided_segments: provided, cited_segments: cited, partial } = coverage;
  if (![total, provided, cited].every(value => typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 80) || typeof partial !== "boolean") return null;
  if ((cited as number) > (provided as number) || (provided as number) > (total as number) || partial !== ((provided as number) < (total as number))) return null;
  if (row.personal_notes !== undefined && (typeof row.personal_notes !== "string" || row.personal_notes.length > 4000)) return null;
  const stages: NonNullable<ReaderDocument["stages"]> = [];
  if (row.analysis !== undefined && row.analysis !== null) {
    const analysis = record(row.analysis);
    if (row.prompt_version !== "evidence-summary-v2" || !analysis || analysis.method !== "bounded-map-synthesis-v1" || !Array.isArray(analysis.stages) || analysis.stages.length < 2 || analysis.stages.length > 8 || partial) return null;
    for (const value of analysis.stages) {
      const stage = record(value);
      if (!stage || stage.analysis !== undefined) return null;
      const parsed = readerDocument({ overview: stage.overview, sections: stage.sections, limitations: stage.limitations, coverage });
      if (!parsed) return null;
      stages.push({ overview: parsed.overview, sections: parsed.sections, limitations: parsed.limitations });
    }
  }
  return { overview, sections, limitations: [...row.limitations], personal: typeof row.personal_notes === "string" ? row.personal_notes : null,
    ...(stages.length ? { stages } : {}),
    coverage: { total_segments: total as number, provided_segments: provided as number, cited_segments: cited as number, partial } };
}
export type ReaderEvidence = { quotes: { key: string; quote: string }[]; original: string; provenance: string; limitations: string[] };
export function readerEvidence(value: unknown, noteId: string, revisionId: string, snapshotId: string, keys: string[]): ReaderEvidence | null {
  const row = record(value), revision = record(row?.revision), source = record(row?.source), snapshot = record(row?.snapshot);
  if (!row || row.schema !== "knowledge-note-export-v1" || row.note_id !== noteId || revision?.id !== revisionId || snapshot?.id !== snapshotId || !source || typeof source.original_url !== "string" || source.original_url.length > 8192 || !["user_import", "platform_collection"].includes(String(snapshot.provenance)) || !strings(snapshot.limitations, 20, 2000) || !Array.isArray(row.evidence) || row.evidence.length > 248) return null;
  const quotes: ReaderEvidence["quotes"] = [];
  for (const value of row.evidence) {
    const quote = record(value);
    if (!quote || typeof quote.key !== "string" || !/^E\d{3}$/.test(quote.key) || typeof quote.quote !== "string" || !quote.quote.trim() || quote.quote.length > 800 || quotes.some(other => other.key === quote.key)) return null;
    quotes.push({ key: quote.key, quote: quote.quote });
  }
  if (keys.some(key => !quotes.some(quote => quote.key === key))) return null;
  return { quotes: quotes.filter(quote => keys.includes(quote.key)), original: source.original_url, provenance: snapshot.provenance as string, limitations: [...snapshot.limitations] };
}

export type RecallCard = { id: string; prompt: string; reference: ReaderItem; quotes: ReaderEvidence["quotes"] };
export function recallCards(document: ReaderDocument, evidence: ReaderEvidence): RecallCard[] {
  const entries = [
    { id: "overview", prompt: "不看笔记，用自己的话复述这篇笔记的概述。", reference: document.overview },
    ...document.sections.flatMap(section => section.items.map((reference, index) => ({
      id: `${section.kind}-${index}`, prompt: `不看笔记，回忆「${sectionNames[section.kind]}」第 ${index + 1} 条，并说明需要核对的原文依据。`, reference,
    }))),
  ];
  if (entries.length > 31 || entries.some(entry => entry.reference.evidence_keys.some(key => !evidence.quotes.some(quote => quote.key === key)))) throw new Error("RECALL_EVIDENCE_INVALID");
  return entries.map(entry => ({ ...entry, reference: { ...entry.reference, evidence_keys: [...entry.reference.evidence_keys] },
    quotes: evidence.quotes.filter(quote => entry.reference.evidence_keys.includes(quote.key)).map(quote => ({ ...quote })) }));
}
