import { useEffect, useRef, useState } from "react";
import type { MemorySuggestion } from "./types";

type Props = {
  items: MemorySuggestion[];
  onApprove: (item: MemorySuggestion, edits: Partial<MemorySuggestion>) => void;
  onDismiss: (item: MemorySuggestion) => void;
};

export function MemorySuggestions({ items, onApprove, onDismiss }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [edits, setEdits] = useState<Record<string, Partial<MemorySuggestion>>>({});
  const previousIds = useRef(new Set(items.map((item) => item.id)));

  useEffect(() => {
    const currentIds = new Set(items.map((item) => item.id));
    const hasNewSuggestions = [...currentIds].some((id) => !previousIds.current.has(id));
    if (hasNewSuggestions) setExpanded(false);
    previousIds.current = currentIds;
  }, [items]);

  if (!items.length) return null;

  function updateEdit(id: string, patch: Partial<MemorySuggestion>) {
    setEdits((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  }

  return <section className={`memory-suggestions ${expanded ? "expanded" : "collapsed"}`} aria-label="待确认的记忆建议">
    <button type="button" className="memory-suggestions-toggle" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
      <span><strong>{items.length} 条记忆建议</strong><small>确认后才会保存，你可以稍后处理</small></span>
      <span className="memory-suggestions-toggle-action">{expanded ? "收起" : "查看记忆建议"}<span aria-hidden="true">{expanded ? "⌃" : "⌄"}</span></span>
    </button>
    {expanded && <div className="memory-suggestions-list">
      {items.map((item) => {
        const edit = edits[item.id] || {};
        return <article className="memory-suggestion" key={item.id}>
          <p className="memory-suggestion-content">{edit.content ?? item.content}</p>
          <div className="memory-suggestion-actions">
            <button type="button" onClick={() => onDismiss(item)}>忽略</button>
            <button type="button" className="primary-button" disabled={!(edit.content ?? item.content).trim()} onClick={() => onApprove(item, edit)}>保存</button>
          </div>
          <details className="memory-suggestion-details">
            <summary>编辑内容或查看依据</summary>
            <textarea aria-label="记忆内容" value={edit.content ?? item.content} onChange={(event) => updateEdit(item.id, { content: event.target.value })} />
            <div className="memory-suggestion-meta">
              <label>用途<select value={edit.memory_type ?? item.memory_type} onChange={(event) => updateEdit(item.id, { memory_type: event.target.value as MemorySuggestion["memory_type"] })}>
                <option value="working">正在处理</option><option value="semantic">关于我的习惯</option><option value="episodic">重要经历</option>
              </select></label>
              <label>隐私<select value={edit.sensitivity ?? item.sensitivity} onChange={(event) => updateEdit(item.id, { sensitivity: event.target.value as MemorySuggestion["sensitivity"] })}>
                <option value="normal">普通</option><option value="sensitive">敏感</option><option value="private">严格私密</option>
              </select></label>
            </div>
            <small>依据：{item.evidence}{item.rationale ? ` · ${item.rationale}` : ""}</small>
          </details>
        </article>;
      })}
    </div>}
  </section>;
}
