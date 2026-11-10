"use client";
import { accountFetch as fetch } from "@/lib/account-fetch";

import { useState } from "react";

type Provider = { id: string; kind: string; model: string; endpoint_name: string | null; priority: number; enabled: boolean; has_key: boolean; adapter_verified: boolean };
type Usage = { id: string; model: string; status: string; input_tokens: number | null; output_tokens: number | null; cost_usd: string | null; error_code: string | null };

export default function Models({ token, space }: { token: string; space: string }) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [usage, setUsage] = useState<Usage[]>([]);
  const [endpoints, setEndpoints] = useState<string[]>([]);
  const [kind, setKind] = useState("openai");
  const [model, setModel] = useState("");
  const [secret, setSecret] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [existingKey, setExistingKey] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [priority, setPriority] = useState(10);
  const [consent, setConsent] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [theme, setTheme] = useState("system");
  const [cloudEnabled, setCloudEnabled] = useState(false);
  const [deepseekEnabled, setDeepseekEnabled] = useState(false);
  const [cloudFallback, setCloudFallback] = useState(false);
  const [message, setMessage] = useState("");
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const path = `/api/account/spaces/${space}`;
  async function request(suffix: string, method = "GET", body?: object) {
    const response = await fetch(path + suffix, { method, cache: "no-store", headers: {
      Authorization: `Bearer ${token}`, "Content-Type": "application/json",
    }, ...(body ? { body: JSON.stringify(body) } : {}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.code ?? "REQUEST_FAILED");
    return data.data;
  }
  async function refresh() {
    setProviders(await request("/providers"));
    const options = await request("/model-options");
    setEndpoints(options.ollama_endpoints);
    setCloudEnabled(options.cloud_enabled);
    setDeepseekEnabled(options.deepseek_enabled);
    setUsage(await request("/ai-usage"));
    const settings = await request("/settings");
    setTheme(settings.theme);
    setCloudFallback(settings.allow_cloud_fallback);
  }
  async function action(run: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await run(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "请求失败"); }
    finally { setBusy(false); }
  }
  function resetForm() {
    setEditing(null); setExistingKey(false); setSecret(""); setModel("");
    setEndpoint(""); setEnabled(false); setConsent(false); setPriority(10);
  }
  function editProvider(provider: Provider) {
    setEditing(provider.id); setKind(provider.kind); setModel(provider.model);
    setEndpoint(provider.endpoint_name ?? ""); setPriority(provider.priority);
    setEnabled(provider.enabled); setExistingKey(provider.has_key); setSecret("");
    setConsent(false); setMessage("编辑配置；启用时请重新勾选授权。");
  }
  return <section aria-label="模型与隐私设置">
    <h2>模型与隐私设置</h2>
    <p>仅空间管理员管理供应商。云端调用需运维开放并由你授权费用；本地模型需运维配置授权端点。</p>
    <button disabled={busy} onClick={() => void action(refresh)}>加载模型设置</button>
    <p><label>供应商<select disabled={busy || editing !== null} value={kind} onChange={event => { setKind(event.target.value); setSecret(""); setEnabled(false); }}><option value="openai">OpenAI{cloudEnabled ? "" : "（运维未开放调用）"}</option><option value="deepseek">DeepSeek{deepseekEnabled ? "" : "（运维未开放调用）"}</option><option value="ollama">Ollama（授权本地端点）</option></select></label></p>
    <p><label>模型名称<input value={model} maxLength={100} onChange={event => setModel(event.target.value)} /></label></p>
    {kind === "openai" || kind === "deepseek" ? <p><label>供应商密钥<input type="password" autoComplete="off" value={secret} onChange={event => setSecret(event.target.value)} /></label></p> : <p><label>授权端点<select value={endpoint} onChange={event => setEndpoint(event.target.value)}><option value="">请选择</option>{endpoints.map(name => <option key={name}>{name}</option>)}</select></label></p>}
    {editing && existingKey && <p>密钥留空会保留当前密钥；输入新密钥即可替换。停用并清除密钥后，云端配置须填入新密钥才能启用。</p>}
    <p><label>调用优先级<input type="number" min={0} max={100} value={priority} onChange={event => setPriority(Number(event.target.value))} /></label>（数字越小越优先）</p>
    <p><label><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} />启用此配置（需运维开放并勾选授权）</label></p>
    <p><label><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />我授权将测试文本发送到启用的供应商，并承担云端调用费用。</label></p>
    <button disabled={busy || !model || (enabled && !consent)} onClick={() => void action(async () => {
      await request(editing ? `/providers/${editing}/update` : "/providers", "POST", { kind, model, ...(secret ? { api_key: secret } : {}),
        ...(kind === "ollama" ? { endpoint_name: endpoint } : {}), enabled, consent, priority });
      resetForm(); await refresh(); setMessage("配置已保存，密钥不会回显。");
    })}>{editing ? "保存配置修改" : "保存模型配置"}</button>{" "}
    {editing && <button disabled={busy} onClick={() => { resetForm(); setMessage("已取消编辑。"); }}>取消编辑</button>}
    <p>每个空间最多保存 5 条配置；可编辑已有配置更换模型或密钥，历史调用记录会保留。</p>
    <ul>{providers.map(provider => <li key={provider.id}>{provider.kind} · {provider.model} · {provider.enabled ? "启用" : "禁用"} · {provider.has_key ? "已加密密钥" : "无密钥"}{" "}<button disabled={busy} onClick={() => editProvider(provider)}>编辑配置</button>{" "}<button disabled={busy} onClick={() => void action(async () => { await request(`/providers/${provider.id}/disable`, "POST"); if (editing === provider.id) resetForm(); await refresh(); })}>禁用并清除密钥</button>{" "}<button disabled={busy || !provider.has_key} onClick={() => void action(async () => { await request(`/providers/${provider.id}/rotate-encryption`, "POST"); setMessage("已使用运维当前密钥版本重新加密。"); })}>重新加密</button></li>)}</ul>
    <p><label>连接测试文本<textarea value={prompt} maxLength={4000} onChange={event => setPrompt(event.target.value)} /></label></p>
    <button disabled={busy || !consent || !prompt} onClick={() => void action(async () => {
      const data = await request("/ai-test", "POST", { prompt, consent });
      setOutput(data.text); await refresh();
    })}>执行授权连接测试</button>
    {output && <pre style={{ whiteSpace: "pre-wrap" }}>{output}</pre>}
    <h3>账户空间偏好</h3>
    <p><label><input type="checkbox" checked={cloudFallback} onChange={event => setCloudFallback(event.target.checked)} />允许本地失败后将文本回退到同空间已启用且授权的云端模型（可能收费）。</label></p>
    <label>主题偏好<select value={theme} onChange={event => setTheme(event.target.value)}><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label>{" "}
    <button disabled={busy} onClick={() => void action(async () => { await request("/settings", "POST", { theme, allow_cloud_fallback: cloudFallback }); setMessage("偏好已保存到当前账户空间；工作台本机主题仍独立设置。"); })}>保存账户偏好</button>
    <p role="status">{message}</p>
    <h3>真实调用记录</h3>
    <p>每日最多 20 次尝试；未配置真实模型时没有用量。未知 Token/费用显示未知，不估算费用。reserved 表示结果不确定，不自动重试。</p>
    <ul>{usage.map(row => <li key={row.id}>{row.model} · {row.status} · 输入 {row.input_tokens ?? "未知"} / 输出 {row.output_tokens ?? "未知"} Token · 费用 {row.cost_usd ?? "未知"} · {row.error_code}</li>)}</ul>
    <p>此处提供授权连接测试，用量按实际响应记录；内容采集和 AI 总结尚未开发。</p>
  </section>;
}
