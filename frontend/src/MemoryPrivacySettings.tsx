import type { Settings } from "./types";

type Props = {
  draft: Settings;
  onChange: (patch: Partial<Settings>) => void;
};

export function MemoryPrivacySettings({ draft, onChange }: Props) {
  const toggle = (label: string, description: string, key: "use_vector_memory_search") => (
    <div className="privacy-setting">
      <div><strong>{label}</strong><p>{description}</p></div>
      <button type="button" role="switch" aria-checked={draft[key]} className={`toggle ${draft[key] ? "on" : ""}`} onClick={() => onChange({ [key]: !draft[key] } as Partial<Settings>)}><span /></button>
    </div>
  );

  return <>
    {toggle("启用本机向量语义检索", "使用本机 Ollama 嵌入模型查找同义表达；向量生成和索引均在本机进行。", "use_vector_memory_search")}
    <div className="privacy-setting"><div><strong>敏感记忆逐次确认</strong><p>发送前会显示本轮匹配到的敏感记忆，由你决定是否仅在本次请求中附加。严格私密记忆永不发送。</p></div><span className="secure-badge">每次确认</span></div>
    {draft.use_vector_memory_search && <div className="form-grid memory-embedding-fields">
      <label className="field full-field"><span>本机 Ollama 嵌入地址</span><input value={draft.memory_embedding_url} onChange={(event) => onChange({ memory_embedding_url: event.target.value })} /><small>只接受 localhost / 127.0.0.1 的 /api/embed 地址。</small></label>
      <label className="field full-field"><span>嵌入模型名称</span><input value={draft.memory_embedding_model} onChange={(event) => onChange({ memory_embedding_model: event.target.value })} placeholder="例如 embeddinggemma" /><small>需在本机 Ollama 中安装此模型。服务不可用时会回退到关键词检索。</small></label>
    </div>}
  </>;
}
