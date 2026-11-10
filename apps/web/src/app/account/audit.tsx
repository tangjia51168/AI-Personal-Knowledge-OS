"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useState } from "react";

type Event = { id: string; actor_id: string; action: string; resource_id: string; created_at: string };
const actions: Record<string, string> = {
  "space.created": "创建空间", "member.added": "添加成员", "member.removed": "移除成员",
  "job.created": "创建任务", "job.cancelled": "取消任务", "job.retried": "重试任务",
  "file.uploaded": "上传文件", "file.deleted": "删除文件", "provider.created": "添加模型配置",
  "provider.disabled": "停用模型配置", "provider.reencrypted": "轮换凭证加密", "provider.updated": "更新模型配置", "settings.updated": "更新个人设置",
  "source.batch.created": "保存来源候选批次",
  "source.snapshot.created": "保存公开采集快照", "source.content.imported": "保存人工导入快照", "notes.generated": "生成证据笔记",
  "notes.edited": "人工修订笔记",
  "notes.annotated": "追加逐段人工批注",
  "organization.concept_created": "创建人工概念卡片",
  "notes.linked": "建立笔记双向链接", "notes.unlinked": "移除笔记双向链接",
  "organization.label_created": "创建主题或标签", "organization.label_attached": "关联笔记分类", "organization.label_removed": "移除笔记分类关联", "organization.label_updated": "维护主题或标签",
  "learning.updated": "更新学习状态",
  "research.created": "保存证据问答请求", "research.finished": "保存证据问答结果",
  "source.schedule_created": "创建公开来源同步计划", "source.schedule_updated": "暂停或恢复同步计划",
};

export default function Audit({ token, space }: { token: string; space: string }) {
  const [events, setEvents] = useState<Event[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("尚未加载审计记录。");

  async function load(append: boolean) {
    setBusy(true);
    try {
      const query = new URLSearchParams({ limit: "20" });
      if (append && cursor) query.set("cursor", cursor);
      const response = await fetch(`/api/account/spaces/${space}/audit-events?${query}`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.code ?? "AUDIT_UNAVAILABLE");
      setEvents(previous => append ? [...previous, ...result.data] : result.data);
      setCursor(result.next_cursor ?? null);
      setMessage(result.data.length ? "已加载真实审计记录。" : "没有更多记录。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "审计记录加载失败"); }
    finally { setBusy(false); }
  }

  return <section aria-label="空间审计记录">
    <h2>空间审计记录</h2>
    <p>仅管理员可查看关键变更的时间、操作者和资源 ID。</p>
    <button className="button secondary" disabled={busy} onClick={() => void load(false)}>刷新审计记录</button>
    <ol>{events.map(event => <li key={event.id} style={{ marginBlock: 12, overflowWrap: "anywhere" }}>
      <strong>{actions[event.action] ?? event.action}</strong> · <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString()}</time>
      <br />操作者：{event.actor_id}<br />资源：{event.resource_id}
    </li>)}</ol>
    {cursor && <button className="button secondary" disabled={busy} onClick={() => void load(true)}>加载更早记录</button>}
    <p role="status">{message}</p>
  </section>;
}
