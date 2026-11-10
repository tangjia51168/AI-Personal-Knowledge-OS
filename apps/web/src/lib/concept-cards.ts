export type ConceptCard = {
  id: string; note_id: string; revision_id: string; snapshot_id: string;
  name: string; definition: string; evidence_key: string; quote: string;
  method: "manual"; confidence: null; created_by: string; created_at: string;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function conceptCards(value: unknown, note: string, revision: string, snapshot: string): ConceptCard[] {
  if (!Array.isArray(value) || value.length > 20) throw new Error("INVALID_CONCEPT_RESPONSE");
  const ids = new Set<string>();
  for (const entry of value) {
    const row = entry as Partial<ConceptCard> | null;
    if (!row || typeof row !== "object" || Array.isArray(row)
      || typeof row.id !== "string" || !uuid.test(row.id) || ids.has(row.id)
      || row.note_id !== note || row.revision_id !== revision || row.snapshot_id !== snapshot
      || typeof row.name !== "string" || !row.name.trim() || row.name.length > 60
      || typeof row.definition !== "string" || !row.definition.trim() || row.definition.length > 2000
      || typeof row.quote !== "string" || !row.quote.trim() || row.quote.length > 800
      || typeof row.evidence_key !== "string" || !/^E[0-9]{3}$/.test(row.evidence_key)
      || row.method !== "manual" || row.confidence !== null
      || typeof row.created_by !== "string" || !uuid.test(row.created_by)
      || typeof row.created_at !== "string" || !Number.isFinite(Date.parse(row.created_at))) throw new Error("INVALID_CONCEPT_RESPONSE");
    ids.add(row.id);
  }
  return value as ConceptCard[];
}
