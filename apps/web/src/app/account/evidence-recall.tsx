"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Brain, CheckCircle2, RotateCcw } from "lucide-react";
import { accountFetch } from "@/lib/account-fetch";
import { readerDocument, readerEvidence, recallCards, type RecallCard } from "@/lib/note-reader";

type Props = { space: string; noteId: string; revision: { id: string; snapshot_id: string; document: Record<string, unknown> } };
type Rating = "again" | "recalled";

export default function EvidenceRecall({ space, noteId, revision }: Props) {
  const [cards, setCards] = useState<RecallCard[] | null>(null);
  const [queue, setQueue] = useState<string[]>([]);
  const [position, setPosition] = useState(0);
  const [draft, setDraft] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [ratings, setRatings] = useState<Record<string, Rating>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("尚未开始；先读取当前固定修订及原文证据。");
  const request = useRef<AbortController | null>(null);
  const field = useId();
  useEffect(() => () => request.current?.abort(), []);
  const card = cards?.find(item => item.id === queue[position]);
  const wrong = cards?.filter(item => ratings[item.id] === "again") ?? [];

  function reset() {
    setCards(null); setQueue([]); setPosition(0); setDraft(""); setRevealed(false); setRatings({});
  }

  async function start() {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller;
    reset(); setBusy(true); setMessage("正在核对本修订与原文证据…");
    try {
      const document = readerDocument(revision.document);
      if (!document) throw new Error("RECALL_DOCUMENT_INVALID");
      const response = await accountFetch(`/api/account/spaces/${space}/notes/${noteId}/revisions/${revision.id}/export?format=json`, { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("RECALL_READ_FAILED");
      const text = await response.text();
      if (controller.signal.aborted) return;
      if (new TextEncoder().encode(text).length > 1_048_576) throw new Error("RECALL_LIMIT");
      const keys = [document.overview, ...document.sections.flatMap(section => section.items)].flatMap(item => item.evidence_keys);
      const evidence = readerEvidence(JSON.parse(text), noteId, revision.id, revision.snapshot_id, keys);
      if (!evidence) throw new Error("RECALL_EVIDENCE_INVALID");
      const deck = recallCards(document, evidence);
      setCards(deck); setQueue(deck.map(item => item.id));
      setMessage(`已准备 ${deck.length} 张回忆卡，来自当前修订的概述与已生成条目；不是模型出题或自动评分。`);
    } catch {
      if (!controller.signal.aborted) { reset(); setMessage("读取失败，已清空旧题卡、草稿和自评；请手动重试。"); }
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function rate(value: Rating) {
    if (!card || !revealed || !draft.trim()) return;
    setRatings(current => ({ ...current, [card.id]: value }));
    setPosition(current => current + 1); setDraft(""); setRevealed(false);
    setMessage(value === "again" ? "已加入本次待重练；这是你的自评。" : "已记录本次自评，不代表系统确认掌握。");
  }

  return <details className="evidence-recall">
    <summary><Brain size={18} aria-hidden="true" /> 证据自测 · 不调用模型</summary>
    <section aria-label="固定修订证据自测" aria-busy={busy}>
      <h3>先回忆，再对照原文</h3>
      <p>题卡按已有章节生成回忆提示。先写回答，再查看笔记参考和原文，由你自评；引用存在不证明观点正确。个人理解不出题。</p>
      <p className="recall-version">固定修订：{revision.id} · 快照：{revision.snapshot_id}</p>
      <button className="button secondary" disabled={busy} onClick={() => void start()}>{cards ? "重新读取并开始新自测" : "读取证据并开始自测"}</button>
      {cards && <button className="button secondary" disabled={busy} onClick={() => { reset(); setMessage("已清空本次自测；没有写入服务器记录。"); }}>结束并清空自测</button>}
      {card && <div className="recall-card">
        <span className="account-eyebrow">RECALL · {position + 1} / {queue.length}</span>
        <h4>{card.prompt}</h4>
        <label htmlFor={field}>我的回忆回答（仅本次页面）</label>
        <textarea id={field} maxLength={4000} rows={4} value={draft} onChange={event => { setDraft(event.target.value); setRevealed(false); }} />
        <button className="button primary" disabled={!draft.trim() || revealed} onClick={() => setRevealed(true)}>提交回忆并展开参考</button>
        {revealed && <div aria-label="自测参考与证据" role="region">
          <h4>笔记参考（未经事实核验）</h4><p className="reader-text">{card.reference.text}</p>
          {card.quotes.map(quote => <blockquote key={quote.key}><strong>{quote.key}</strong><p className="reader-text">{quote.quote}</p></blockquote>)}
          <p>仅自评本题，不修改阅读状态、提醒时间或掌握率。</p>
          <div className="recall-actions"><button className="button secondary" onClick={() => rate("again")}><RotateCcw size={15} /> 需要再练</button><button className="button primary" onClick={() => rate("recalled")}><CheckCircle2 size={15} /> 自评已回忆</button></div>
        </div>}
      </div>}
      {cards && !card && <div className="recall-card" role="region" aria-label="本次自测结果">
        <h4>本轮已完成</h4><p>本次共 {cards.length} 张；自评已回忆 {cards.filter(item => ratings[item.id] === "recalled").length} 张，待重练 {wrong.length} 张。仅反映你本次的选择。</p>
        {wrong.length > 0 && <><ul>{wrong.map(item => <li key={item.id}>{item.prompt}</li>)}</ul><button className="button secondary" onClick={() => { setQueue(wrong.map(item => item.id)); setPosition(0); setDraft(""); setRevealed(false); setMessage("仅重练本次自评需要再练的题卡。"); }}><RotateCcw size={15} /> 只重练待练题卡</button></>}
      </div>}
      <p role="status" aria-label="证据自测状态">{message}</p>
      <p className="recall-limit">草稿与自评仅保留在当前页面；刷新、换版本、换空间或退出后清空。没有永久错题库、事件历史、通知发送或自动间隔算法。</p>
    </section>
  </details>;
}
