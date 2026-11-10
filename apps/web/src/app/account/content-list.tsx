"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

export type ContentFilters = { q: string; platform: string };
export type ContentPage<T> = { data: T[]; next_cursor: string | null };

export const platformNames: Record<string, string> = {
  web: "普通网页", bilibili: "B站", youtube: "YouTube", douyin: "抖音", xiaohongshu: "小红书", zhihu: "知乎",
};

export function PlatformOptions() {
  return <><option value="">全部来源类型</option>{Object.entries(platformNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</>;
}

export function boundedFilters<T extends ContentFilters>(filters: T): T {
  const q = filters.q.trim();
  if (Array.from(q).length > 120 || new TextEncoder().encode(q).length > 480) throw new Error("关键词最多 120 个字符、480 字节 UTF-8。");
  return { ...filters, q };
}

export async function contentPage<T>(token: string, space: string, kind: "sources" | "notes", filters: ContentFilters, cursor: string | null, signal?: AbortSignal): Promise<ContentPage<T>> {
  const query = new URLSearchParams({ limit: "20" });
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
  if (cursor) query.set("cursor", cursor);
  const response = await fetch(`/api/account/spaces/${space}/${kind}?${query}`, {
    cache: "no-store", signal, headers: { Authorization: `Bearer ${token}` },
  });
  const result = await response.json();
  if (!response.ok) {
    const code = result.error?.code ?? "REQUEST_FAILED";
    if (code === "SOURCE_CURSOR_NOT_FOUND" || code === "NOTE_CURSOR_NOT_FOUND") throw new Error("分页位置已失效，请刷新列表回到首页；已打开的内容和草稿保留。");
    throw new Error(code);
  }
  return { data: result.data, next_cursor: result.next_cursor ?? null };
}

export function ContentPagination({ label, page, nextCursor, disabled, onFirst, onPrevious, onNext }: {
  label: string; page: number; nextCursor: string | null; disabled: boolean;
  onFirst: () => void; onPrevious: () => void; onNext: () => void;
}) {
  return <nav aria-label={`${label}分页`}>
    <button className="button secondary" disabled={disabled || page <= 1} onClick={onFirst}>首页</button>{" "}
    <button className="button secondary" disabled={disabled || page <= 1} onClick={onPrevious}>上一页</button>{" "}
    <span>第 {page} 页 · 每页最多 20 条</span>{" "}
    <button className="button secondary" disabled={disabled || !nextCursor} onClick={onNext}>下一页</button>
  </nav>;
}
