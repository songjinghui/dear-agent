import { useEffect, useState } from "react";
import { api } from "./api";

type LogSummary = {
  id: string; session_id: string | null; provider: string; model: string; purpose: string;
  status: string; created_at: string; duration_ms: number | null; error: string | null; preview: string;
};
type LogDetail = LogSummary & { request: Record<string, unknown>; response_preview: string | null };

function statusLabel(status: string) {
  if (status === "complete") return "已完成";
  if (status === "failed") return "失败";
  return "处理中";
}

export function ModelCallLogs({ onBack }: { onBack: () => void }) {
  const [items, setItems] = useState<LogSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<LogDetail | null>(null);
  const [error, setError] = useState("");
  const refresh = async () => {
    try {
      const result = await api<{ items: LogSummary[]; total: number }>(`/api/model-call-logs?limit=100&offset=${offset}`);
      setItems(result.items); setTotal(result.total); setError("");
    } catch (e) { setError((e as Error).message); }
  };
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer); }, [offset]);
  useEffect(() => {
    if (!selected) return;
    const timer = window.setInterval(() => {
      void api<LogDetail>(`/api/model-call-logs/${selected.id}`).then(setSelected).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [selected?.id]);

  async function openLog(item: LogSummary) {
    try { setSelected(await api<LogDetail>(`/api/model-call-logs/${item.id}`)); setError(""); }
    catch (e) { setError((e as Error).message); }
  }
  async function removeLog(item: LogSummary) {
    if (!window.confirm("删除这条本地模型调用记录？")) return;
    try { await api(`/api/model-call-logs/${item.id}`, { method: "DELETE" }); setSelected(null); await refresh(); }
    catch (e) { setError((e as Error).message); }
  }
  async function clearLogs() {
    if (!window.confirm("删除全部本地模型调用记录？")) return;
    try { await api("/api/model-call-logs", { method: "DELETE" }); setSelected(null); setOffset(0); await refresh(); }
    catch (e) { setError((e as Error).message); }
  }

  return <section className="content-page model-logs-page">
    <div className="page-title-row"><div><span className="eyebrow">LOCAL MODEL ACTIVITY</span><h1>模型调用记录</h1><p>自动保存在本机，可查看请求内容和处理结果。</p></div><button className="secondary-button" onClick={onBack}>返回模型设置</button></div>
    {error && <div className="log-inline-error">{error}</div>}
    <div className="model-logs-layout">
      <div className="settings-card model-log-list-card">
        <div className="model-log-list-heading"><strong>{total} 条记录</strong>{total > 0 && <button className="delete-button" onClick={() => void clearLogs()}>清空记录</button>}</div>
        {!items.length ? <div className="model-log-empty">还没有模型调用记录。下一次模型请求会自动记录在这里。</div> : items.map((item) => <button key={item.id} className={`model-log-row ${selected?.id === item.id ? "selected" : ""}`} onClick={() => void openLog(item)}>
          <span className={`model-log-status status-${item.status}`}>{statusLabel(item.status)}</span><strong>{item.purpose}</strong>
          <small>{new Date(item.created_at).toLocaleString("zh-CN")} · {item.provider}{item.model ? ` · ${item.model}` : ""}</small>
          <span className="model-log-preview">{item.error || item.preview || "查看本次请求详情"}</span>
        </button>)}
        {total > 100 && <div className="model-log-pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>更新</button><span>{offset + 1}–{Math.min(offset + items.length, total)} / {total}</span><button disabled={offset + 100 >= total} onClick={() => setOffset(offset + 100)}>更早记录</button></div>}
      </div>
      <div className="settings-card model-log-detail-card">
        {!selected ? <div className="model-log-empty">选择一条记录，查看实际发送的内容。</div> : <>
          <div className="model-log-detail-heading"><div><span className={`model-log-status status-${selected.status}`}>{statusLabel(selected.status)}</span><h2>{selected.purpose}</h2><p>{new Date(selected.created_at).toLocaleString("zh-CN")} · {selected.provider}{selected.model ? ` · ${selected.model}` : ""}{selected.duration_ms !== null ? ` · ${selected.duration_ms} 毫秒` : ""}</p></div><button className="delete-button" onClick={() => void removeLog(selected)}>删除</button></div>
          {selected.session_id?.startsWith("archive-analysis:") && <p className="model-log-note">这是历史档案分析调用。</p>}
          <h3>发送给模型的请求</h3><pre className="model-log-json">{JSON.stringify(selected.request, null, 2)}</pre>
          {selected.error && <><h3>错误信息</h3><pre className="model-log-json">{selected.error}</pre></>}
          {selected.response_preview && <><h3>模型返回内容（节选）</h3><pre className="model-log-json">{selected.response_preview}</pre></>}
          {selected.request.provider_context_note && <p className="model-log-note">{String(selected.request.provider_context_note)}</p>}
        </>}
      </div>
    </div>
    <p className="model-log-privacy-note">记录保存在本机数据库中，不包含 API Key。日志可能含有对话中的个人内容；可随时删除。</p>
  </section>;
}
