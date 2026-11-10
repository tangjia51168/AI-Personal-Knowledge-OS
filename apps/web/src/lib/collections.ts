export type Collection = { id: string; kind: "folder" | "collection"; name: string; active: boolean; version: number; created_at: string; updated_at: string };
export type CollectionItem = { source_id: string; position: number; url: string };
export type CollectionDetail = { collection: Collection; items: CollectionItem[] };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function parseCollections(value: unknown): Collection[] {
  if (!Array.isArray(value) || value.length > 100 || new Set(value.map(row => row?.id)).size !== value.length) throw new Error("INVALID_COLLECTION_RESPONSE");
  for (const row of value) if (!row || !uuid.test(row.id) || !["folder", "collection"].includes(row.kind) || typeof row.name !== "string" || row.name.length < 1 || row.name.length > 60 || typeof row.active !== "boolean" || !Number.isSafeInteger(row.version) || row.version < 1 || !Number.isFinite(Date.parse(row.created_at)) || !Number.isFinite(Date.parse(row.updated_at))) throw new Error("INVALID_COLLECTION_RESPONSE");
  return value;
}
export function parseCollectionDetail(value: unknown): CollectionDetail {
  const page = value as CollectionDetail;
  if (!page || !Array.isArray(page.items) || page.items.length > 200) throw new Error("INVALID_COLLECTION_RESPONSE");
  parseCollections([page.collection]);
  if (new Set(page.items.map(row => row?.source_id)).size !== page.items.length) throw new Error("INVALID_COLLECTION_RESPONSE");
  for (const row of page.items) {
    if (!row || !uuid.test(row.source_id) || !Number.isInteger(row.position) || row.position < 0 || row.position > 199 || typeof row.url !== "string" || row.url.length > 2048) throw new Error("INVALID_COLLECTION_RESPONSE");
    const url = new URL(row.url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("INVALID_COLLECTION_RESPONSE");
  }
  return page;
}
