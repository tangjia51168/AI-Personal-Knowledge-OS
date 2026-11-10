"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useEffect, useId, useRef, useState } from "react";
import { parseSnapshotEvidence } from "@/lib/research-answer";

type Evidence = { key: string; text: string };
type Props = { token: string; space: string; snapshot: { id: string; title: string } };

export default function ManualNote({ token, space, snapshot }: Props) {
  const id = useId();
  const [title, setTitle] = useState(snapshot.title);
  const requestKey = useRef(crypto.randomUUID());
  const generation = useRef(0);
  const submission = useRef<AbortController | null>(null);
  const [overview, setOverview] = useState("");
  const [personal, setPersonal] = useState("");
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [loadingEvidence, setLoadingEvidence] = useState(true);
  const [created, setCreated] = useState(false);
  const [evidenceAttempt, setEvidenceAttempt] = useState(0);
  useEffect(() => () => submission.current?.abort(), []);

  function invalidateDraft() { requestKey.current = crypto.randomUUID(); setConsent(false); setCreated(false); }

  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    void fetch(`/api/account/spaces/${space}/snapshots/${snapshot.id}/note-evidence`, { cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: controller.signal })
      .then(async response => {
        const value = await response.json();
        if (!response.ok) throw new Error(value.error?.code ?? "NOTE_SOURCE_INVALID");
        if (generation.current !== current) return;
        const parsed = parseSnapshotEvidence(value.data, snapshot.id);
        const rows = parsed.evidence;
        setEvidence(rows); setSelectedKeys(rows.length ? [rows[0].key] : []);
        setMessage(rows.length ? `已加载 ${rows.length} 个可引用快照片段${parsed.partial ? "（仅显示有界前缀）" : ""}。` : "快照没有可引用正文。");
      }).catch(error => { if (!controller.signal.aborted && generation.current === current) setMessage(error instanceof Error ? error.message : "证据读取失败"); })
      .finally(() => { if (!controller.signal.aborted && generation.current === current) setLoadingEvidence(false); });
    return () => { controller.abort(); generation.current += 1; };
  }, [space, snapshot.id, token, evidenceAttempt]);
  async function create() {
    if (!consent || submission.current || created) return;
    const controller = new AbortController(); submission.current = controller;
    setBusy(true); setMessage("");
    try {
      if (!title.trim() || !overview.trim()) throw new Error("请填写标题和概述");
      if (!selectedKeys.length) throw new Error("至少选择一个真实快照片段");
      const response = await fetch(`/api/account/spaces/${space}/snapshots/${snapshot.id}/notes`, {
        method: "POST", cache: "no-store", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({ idempotency_key: requestKey.current, consent: true, title, overview: { text: overview, evidence_keys: selectedKeys }, sections: [{ kind: "core_claims", items: [{ text: overview, evidence_keys: selectedKeys }] }], personal_notes: personal }),
      });
      const value = await response.json();
      if (controller.signal.aborted) return;
      if (!response.ok) throw new Error(value.error?.code ?? "NOTE_CREATE_FAILED");
      setCreated(true); setMessage(`已创建人工证据笔记（版本 ${value.data.version}）。现在可在知识笔记中检索、修订、复习和导出。`); setConsent(false);
    } catch (error) { if (!controller.signal.aborted) setMessage(`${error instanceof Error ? error.message : "请求失败"}；结果可能尚未确认，未修改草稿时重试使用原幂等键。`); }
    finally { if (submission.current === controller) { submission.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }
  return <details className="manual-note-create"><summary>不调用模型，创建人工证据笔记</summary>
    <p>只引用服务器从当前快照切出的真实片段；引用编号用于定位，不证明观点正确。创建成功后表单锁定，避免重复创建同一来源笔记。</p>
    {!loadingEvidence && !evidence.length && <button className="button secondary" disabled={busy || created} onClick={() => { setLoadingEvidence(true); setEvidence([]); setSelectedKeys([]); setConsent(false); setEvidenceAttempt(value => value + 1); }}>重试读取快照证据</button>}
    {loadingEvidence ? <p>正在读取可引用证据…</p> : evidence.length > 0 ? <fieldset className="manual-evidence"><legend>选择要绑定的快照片段（1–8项）</legend>{evidence.map(item => <label key={item.key}><input type="checkbox" checked={selectedKeys.includes(item.key)} disabled={busy || created || (selectedKeys.length >= 8 && !selectedKeys.includes(item.key))} onChange={event => { setSelectedKeys(event.target.checked ? [...selectedKeys, item.key] : selectedKeys.filter(key => key !== item.key)); invalidateDraft(); }} /><b>{item.key}</b><span>{item.text}</span></label>)}</fieldset> : null}
    <label htmlFor={`${id}-title`}>笔记标题<input id={`${id}-title`} value={title} maxLength={200} disabled={busy || created} onChange={event => { setTitle(event.target.value); invalidateDraft(); }} /></label>
    <label htmlFor={`${id}-overview`}>人工概述（必须引用已选片段）<textarea id={`${id}-overview`} value={overview} maxLength={1200} disabled={busy || created} onChange={event => { setOverview(event.target.value); invalidateDraft(); }} /></label>
    <p>第一版将这段人工概述同时保存为核心观点；创建后可在人工修订中拆分章节。仅接受纯文本，不填写 HTML 或链接。</p>
    <label htmlFor={`${id}-personal`}>个人理解（可选，未经来源核验）<textarea id={`${id}-personal`} value={personal} maxLength={4000} disabled={busy || created} onChange={event => { setPersonal(event.target.value); invalidateDraft(); }} /></label>
    <label className="manual-consent"><input type="checkbox" checked={consent} disabled={busy || created || loadingEvidence || !evidence.length} onChange={event => setConsent(event.target.checked)} />我确认这是人工整理，不调用模型，并同意绑定上面选中的证据</label>
    <p><button className="button primary" disabled={busy || created || !consent || !overview.trim() || !selectedKeys.length} onClick={() => void create()}>{busy ? "处理中…" : created ? "已创建" : "创建人工证据笔记"}</button></p><p role="status">{message}</p>
  </details>;
}
