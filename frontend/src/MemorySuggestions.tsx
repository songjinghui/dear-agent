import { useEffect, useRef, useState } from "react";
import "./MemorySuggestions.css";
import { groupMemorySuggestions, type MemorySuggestionGroup } from "./memorySuggestionGroups";
import type { MemorySuggestion } from "./types";

type Props = {
  items: MemorySuggestion[];
  sessionTitles: Record<string, string>;
  onApproveGroup: (group: MemorySuggestionGroup, edits: MemorySuggestionGroupEdit) => void;
  onDismissGroup: (group: MemorySuggestionGroup) => void;
};

type MemorySuggestionGroupEdit = Pick<MemorySuggestion, "content" | "memory_type" | "importance" | "sensitivity" | "expires_at">;

export function MemorySuggestions({ items, sessionTitles, onApproveGroup, onDismissGroup }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [edits, setEdits] = useState<Record<string, MemorySuggestionGroupEdit>>({});
  const previousIds = useRef(new Set(items.map((item) => item.id)));
  const groups = groupMemorySuggestions(items, sessionTitles);

  useEffect(() => {
    const currentIds = new Set(items.map((item) => item.id));
    const hasNewSuggestions = [...currentIds].some((id) => !previousIds.current.has(id));
    if (hasNewSuggestions) setExpanded(false);
    previousIds.current = currentIds;
  }, [items]);

  if (!groups.length) return null;

  function updateEdit(id: string, patch: Partial<MemorySuggestionGroupEdit>) {
    setEdits((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  }

  return <section className={`memory-suggestions ${expanded ? "expanded" : "collapsed"}`} aria-label="待确认的记忆建议">
    <button type="button" className="memory-suggestions-toggle" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
      <span><strong>{groups.length} 个待确认主题</strong><small>包含 {items.length} 项详情，确认后会保存为一条主题记忆</small></span>
      <span className="memory-suggestions-toggle-action">{expanded ? "收起" : "查看记忆建议"}<span aria-hidden="true">{expanded ? "⌃" : "⌄"}</span></span>
    </button>
    {expanded && <div className="memory-suggestions-list">
      {groups.map((group) => <section className="memory-suggestion-group" key={group.id}>
        {(() => {
          const edit = edits[group.id] || {};
          const content = edit.content ?? group.content;
          return <article className="memory-suggestion">
            <div className="memory-suggestion-group-heading"><strong>{group.title}</strong><small>{group.items.length} 项详情 · 保存为一条记忆</small></div>
            <ul className="memory-topic-details">{group.items.map((item) => <li key={item.id}>{item.content}</li>)}</ul>
            <div className="memory-suggestion-actions">
              <button type="button" onClick={() => onDismissGroup(group)}>忽略这组</button>
              <button type="button" className="primary-button" disabled={!content.trim()} onClick={() => onApproveGroup(group, { ...edit, content })}>保存这组</button>
            </div>
            <details className="memory-suggestion-details">
              <summary>编辑主题内容或查看依据</summary>
              <textarea aria-label="主题记忆内容" value={content} onChange={(event) => updateEdit(group.id, { content: event.target.value })} />
              <div className="memory-suggestion-meta">
                <label>用途<select value={edit.memory_type ?? group.memoryType} onChange={(event) => updateEdit(group.id, { memory_type: event.target.value as MemorySuggestion["memory_type"] })}>
                  <option value="working">正在处理</option><option value="semantic">关于我的习惯</option><option value="episodic">重要经历</option>
                </select></label>
                <label>隐私<select value={edit.sensitivity ?? group.sensitivity} onChange={(event) => updateEdit(group.id, { sensitivity: event.target.value as MemorySuggestion["sensitivity"] })}>
                  <option value="normal">普通</option><option value="sensitive">敏感</option><option value="private">严格私密</option>
                </select></label>
              </div>
              <ul className="memory-topic-evidence">{group.items.map((item) => <li key={item.id}>依据：{item.evidence}{item.rationale ? ` · ${item.rationale}` : ""}</li>)}</ul>
            </details>
          </article>;
        })()}
      </section>)}
    </div>}
  </section>;
}
