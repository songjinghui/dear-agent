import { useState } from "react";
import { api } from "./api";
import { evidenceLabels } from "./constants";
import type { ArchiveAnalysis, CandidateOrganizationGroup, MemoryCandidate, MemorySensitivity } from "./types";

type Props = {
  analysis: ArchiveAnalysis;
  onRefresh: () => Promise<void>;
  onOpenReference: (sessionId: string, sequence: number) => void;
};

export function ArchiveCandidateOrganization({ analysis, onRefresh, onOpenReference }: Props) {
  const [busy, setBusy] = useState(false);
  const organization = analysis.organization;

  async function decide(group: CandidateOrganizationGroup, action: "approve" | "reject") {
    if (!analysis.job) return;
    setBusy(true);
    try {
      await api(`/api/archives/analysis-jobs/${encodeURIComponent(analysis.job.id)}/organization/${encodeURIComponent(group.id)}`, {
        method: "PUT", body: JSON.stringify({ action }),
      });
      await onRefresh();
    } catch (error) {
      window.alert((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <>
    {organization && <section className="candidate-organization">
      {organization.status === "queued" || organization.status === "processing" ? <p className="analysis-progress-note">正在比较候选之间的人物和主题关系…</p> : null}
      {organization.status === "waiting_retry" && <p className="analysis-progress-note">{organization.error} {organization.retry_after ? `预计 ${new Date(organization.retry_after).toLocaleString("zh-CN", { hour12: false })} 自动继续。` : "额度恢复后会自动继续。"}</p>}
      {organization.error && <p className="analysis-error">{organization.error}</p>}
      {organization.status === "complete" && <>
        <div className="archive-list-heading">已按人物和主题整理 <span>{organization.groups.filter((item) => item.status === "pending").length} 组待审核</span></div>
        {organization.groups.map((group) => <article className={`memory-candidate ${group.status}`} key={group.id}>
          <div className="candidate-top"><strong>{group.topic}</strong><span>{group.candidate_ids.length} 条相关候选 · 重要度 {group.importance}/5{group.sensitivity === "private" ? " · 严格私密" : group.sensitivity === "sensitive" ? " · 敏感" : ""}</span></div>
          <p className="organization-content">{group.content}</p>
          <small>{group.rationale}</small>
          <div className="candidate-citations">依据：{group.source_refs.map((reference) => <button key={`${reference.message_id}-${reference.sequence}`} onClick={() => onOpenReference(reference.session_id, reference.sequence)}>#{reference.sequence}</button>)}</div>
          {group.status === "pending" ? <div className="candidate-actions"><button disabled={busy} onClick={() => void decide(group, "reject")}>忽略整组</button><button className="primary-button" disabled={busy} onClick={() => void decide(group, "approve")}>采纳为一条记忆</button></div> : <div className="candidate-status">{group.status === "approved" ? "已写入长期记忆" : "已忽略"}</div>}
        </article>)}
      </>}
    </section>}
  </>;
}

export function OriginalCandidates({
  candidates, edits, onEdit, onSensitivity, onDecision, onOpenReference,
}: {
  candidates: MemoryCandidate[];
  edits: Record<string, string>;
  onEdit: (id: string, content: string) => void;
  onSensitivity: (id: string, sensitivity: MemorySensitivity) => void;
  onDecision: (candidate: MemoryCandidate, action: "approve" | "reject") => void;
  onOpenReference: Props["onOpenReference"];
}) {
  const topics = new Map<string, MemoryCandidate[]>();
  for (const candidate of candidates) {
    const topic = candidate.topic?.trim() || "其他主题";
    topics.set(topic, [...(topics.get(topic) ?? []), candidate]);
  }
  return <div className="candidate-list"><div className="archive-list-heading">原始记忆候选 <span>{candidates.filter((item) => item.status === "pending").length} 条待审核</span></div>
    {[...topics.entries()].map(([topic, entries]) => <section className="candidate-topic-group" key={topic}>
      <h4>{topic}</h4>
      {entries.map((candidate) => <article className={`memory-candidate ${candidate.status}`} key={candidate.id}>
        <div className="candidate-top"><strong>{candidate.memory_type === "semantic" ? "偏好/稳定信息" : candidate.memory_type === "working" ? "短期事项" : "重要经历"}</strong><span>{evidenceLabels[candidate.evidence_type] || "待判断"} · 重要度 {candidate.importance}/5{candidate.sensitivity === "private" ? " · 严格私密" : candidate.sensitivity === "sensitive" ? " · 敏感" : ""}</span></div>
        {candidate.status === "pending" ? <textarea value={edits[candidate.id] ?? candidate.content} onChange={(event) => onEdit(candidate.id, event.target.value)} rows={2} /> : <p>{edits[candidate.id] ?? candidate.content}</p>}
        <small>{candidate.rationale}</small>
        <div className="candidate-citations">依据：{candidate.source_refs.map((reference) => <button key={`${reference.message_id}-${reference.sequence}`} onClick={() => onOpenReference(reference.session_id, reference.sequence)}>#{reference.sequence}</button>)}</div>
        {candidate.status === "pending" ? <div className="candidate-actions"><label>敏感级别<select value={candidate.sensitivity} onChange={(event) => onSensitivity(candidate.id, event.target.value as MemorySensitivity)}><option value="normal">普通</option><option value="sensitive">敏感（需单独授权）</option><option value="private">严格私密</option></select></label><button onClick={() => onDecision(candidate, "reject")}>忽略</button><button className="primary-button" onClick={() => onDecision(candidate, "approve")}>采纳为记忆</button></div> : <div className="candidate-status">{candidate.status === "approved" ? "已写入长期记忆" : candidate.status === "superseded" ? "已归入主题记忆" : "已忽略"}</div>}
      </article>)}
    </section>)}
  </div>;
}
