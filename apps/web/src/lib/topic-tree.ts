export type TopicLabel = { id: string; kind: "topic" | "tag"; name: string; active?: boolean; parent_id?: string | null };

export function topicPath(labels: TopicLabel[], id: string): string {
  const byId = new Map(labels.map(item => [item.id, item]));
  if (labels.length > 100 || byId.size !== labels.length) throw new Error("主题目录超限或重复，请重新读取。");
  const seen = new Set<string>();
  const names: string[] = [];
  let current: string | null = id;
  while (current !== null) {
    if (seen.has(current) || seen.size >= 100) throw new Error("主题层级响应有循环或超限，请重新读取。");
    seen.add(current);
    const row = byId.get(current);
    if (!row || !["topic", "tag"].includes(row.kind) || (current !== id && row.kind !== "topic") || (row.kind === "tag" && row.parent_id != null) || (row.parent_id != null && typeof row.parent_id !== "string")) throw new Error("主题层级响应不完整，请重新读取。");
    names.unshift(row.name + (row.active === false ? "（已归档）" : ""));
    current = row.parent_id ?? null;
  }
  return names.join(" / ");
}

export function possibleTopicParents(labels: TopicLabel[], id: string): TopicLabel[] {
  // Exclude descendants without guessing any semantic relationship.
  const byId = new Map(labels.map(item => [item.id, item]));
  if (labels.length > 100 || byId.size !== labels.length) return [];
  return labels.filter(item => {
    if (item.kind !== "topic" || item.active === false || item.id === id) return false;
    const seen = new Set<string>();
    let current: string | null = item.id;
    while (current !== null) {
      if (current === id || seen.has(current) || seen.size >= 100) return false;
      seen.add(current);
      const ancestor = byId.get(current);
      if (!ancestor || ancestor.kind !== "topic") return false;
      current = ancestor.parent_id ?? null;
    }
    return true;
  });
}
