import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { AssistantReply } from "./AssistantReply";
import { ImageAttachments } from "./ImageAttachments";
import { useChatSessionSync } from "./useChatSessionSync";
import { ModelCallLogs } from "./ModelCallLogs";
import { MemoryPrivacySettings } from "./MemoryPrivacySettings";
import { MemorySuggestions } from "./MemorySuggestions";
import { pageFromPathname, pathForPage } from "./routes";
import { ArchiveCandidateOrganization, OriginalCandidates } from "./ArchiveCandidateOrganization";
import { emptySettings, formatBytes, labels } from "./constants";
import type { ArchiveAnalysis, ArchiveImport, ArchivePage, ArchivePreview, ArchiveSession, AnalysisReference, Memory, MemoryCandidate, MemorySuggestion, Message, Page, Session, Settings, Theme } from "./types";

function mergeMemorySuggestions(current: MemorySuggestion[], updates: MemorySuggestion[]) {
  const byId = new Map(current.map((item) => [item.id, item]));
  updates.forEach((item) => byId.set(item.id, item));
  return [...byId.values()];
}

function App() {
  const [page, setCurrentPage] = useState<Page>(() => pageFromPathname(window.location.pathname));
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [memorySuggestions, setMemorySuggestions] = useState<MemorySuggestion[]>([]);
  const [memoryTypeFilter, setMemoryTypeFilter] = useState<"all" | Memory["memory_type"]>("all");
  const [settings, setSettings] = useState<Settings>(emptySettings);
  const [draft, setDraft] = useState("");
  const [memorySearch, setMemorySearch] = useState("");
  const [editingMemory, setEditingMemory] = useState<Memory | null | "new">(null);
  const [busy, setBusy] = useState(false);
  const [sessionCreationPending, setSessionCreationPending] = useState(false);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [animatedMessageId, setAnimatedMessageId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [settingsDraft, setSettingsDraft] = useState(emptySettings);
  const [archiveImports, setArchiveImports] = useState<ArchiveImport[]>([]);
  const [archiveSessions, setArchiveSessions] = useState<ArchiveSession[]>([]);
  const [archiveTotal, setArchiveTotal] = useState(0);
  const [archiveOffset, setArchiveOffset] = useState(0);
  const [archiveQuery, setArchiveQuery] = useState("");
  const [archiveHits, setArchiveHits] = useState<(Message & { session_id: string; title: string; source_platform: string })[]>([]);
  const [selectedArchive, setSelectedArchive] = useState<ArchivePage | null>(null);
  const [editingArchiveTitle, setEditingArchiveTitle] = useState(false);
  const [archiveTitleDraft, setArchiveTitleDraft] = useState("");
  const [showArchiveActions, setShowArchiveActions] = useState(false);
  const [archiveAnalysis, setArchiveAnalysis] = useState<ArchiveAnalysis>({ job: null, candidates: [] });
  const [candidateEdits, setCandidateEdits] = useState<Record<string, string>>({});
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveImportReport, setArchiveImportReport] = useState("");
  const [archivePreview, setArchivePreview] = useState<(ArchivePreview & { file: File }) | null>(null);
  const [archiveSelection, setArchiveSelection] = useState<Record<string, boolean>>({});
  const [archivePreviewQuery, setArchivePreviewQuery] = useState("");
  const [archivePreviewPage, setArchivePreviewPage] = useState(0);
  const archiveAnalysisRunning = ["queued", "processing", "waiting_retry"].includes(archiveAnalysis.job?.status || "");
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("companion-theme");
    return saved === "mist" || saved === "night" ? saved : "warm";
  });
  const [showThemeMenu, setShowThemeMenu] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const archiveFileRef = useRef<HTMLInputElement>(null);
  const creatingSessionRef = useRef(false);
  const archiveFileQueueRef = useRef<File[]>([]);
  const archiveReportsRef = useRef<string[]>([]);
  const pendingAssistantReplacementRef = useRef<{ localId: string; message: Message } | null>(null);
  const navigate = useCallback((nextPage: Page) => {
    const pathname = pathForPage(nextPage);
    if (window.location.pathname !== pathname) window.history.pushState(null, "", pathname);
    setCurrentPage(nextPage);
  }, []);
  const setPage = navigate;
  const filteredArchiveConversations = useMemo(() => {
    const query = archivePreviewQuery.trim().toLocaleLowerCase();
    const conversations = archivePreview?.conversations ?? [];
    return query ? conversations.filter((item) => item.title.toLocaleLowerCase().includes(query)) : conversations;
  }, [archivePreview, archivePreviewQuery]);
  const chatSync = useChatSessionSync(page === "chat", sessionId, busy, setMessages);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("companion-theme", theme);
  }, [theme]);
  useEffect(() => {
    const onPopState = () => setCurrentPage(pageFromPathname(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  const refreshSessions = useCallback(async () => setSessions(await api<Session[]>("/api/sessions")), []);
  const refreshMemories = useCallback(async (query = "") => {
    setMemories(await api<Memory[]>(`/api/memories${query ? `?q=${encodeURIComponent(query)}` : ""}`));
  }, []);
  const refreshArchiveList = useCallback(async (query = "", offset = 0) => {
    const [imports, sessions] = await Promise.all([
      api<ArchiveImport[]>("/api/archives/imports"),
      api<{ items: ArchiveSession[]; total: number }>(`/api/archives/sessions?q=${encodeURIComponent(query)}&limit=50&offset=${offset}`),
    ]);
    setArchiveImports(imports); setArchiveSessions(sessions.items); setArchiveTotal(sessions.total);
  }, []);
  useEffect(() => {
    void (async () => {
      try {
        const [items, config] = await Promise.all([
          api<Session[]>("/api/sessions"), api<Settings>("/api/settings"),
        ]);
        setSessions(items);
        setSettings(config);
        setSettingsDraft(config);
        const session = items[0] ?? await api<Session>("/api/sessions", { method: "POST", body: JSON.stringify({ title: "新的对话" }) });
        setSessionId(session.id);
        if (!items.length) setSessions([session]);
      } catch (e) { setError((e as Error).message); }
    })();
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);
  useEffect(() => { if (page === "memories") void refreshMemories(memorySearch).catch((e) => setError(e.message)); }, [page, memorySearch, refreshMemories]);
  useEffect(() => {
    if (!sessionId) return;
    api<MemorySuggestion[]>(`/api/memory-suggestions?session_id=${encodeURIComponent(sessionId)}`)
      .then(setMemorySuggestions).catch((e) => setError(e.message));
  }, [sessionId]);
  useEffect(() => {
    if (page !== "archives") return;
    void refreshArchiveList(archiveQuery, archiveOffset).catch((e) => setError(e.message));
  }, [page, archiveQuery, archiveOffset, refreshArchiveList]);
  useEffect(() => {
    const job = archiveAnalysis.job;
    const organization = archiveAnalysis.organization;
    const analysisRunning = !!job && ["queued", "processing", "waiting_retry"].includes(job.status);
    const organizationRunning = !!organization && ["queued", "processing", "waiting_retry"].includes(organization.status);
    if (page !== "archives" || !selectedArchive || (!analysisRunning && !organizationRunning)) return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const latest = await api<ArchiveAnalysis>(`/api/archives/sessions/${encodeURIComponent(selectedArchive.session.id)}/analysis`);
        if (!active) return;
        setArchiveAnalysis(latest);
        const jobRunning = !!latest.job && ["queued", "processing", "waiting_retry"].includes(latest.job.status);
        const orgRunning = !!latest.organization && ["queued", "processing", "waiting_retry"].includes(latest.organization.status);
        if (!jobRunning && !orgRunning) return;
      } catch (e) {
        if (active) setError((e as Error).message);
      }
      if (active) timer = window.setTimeout(() => void poll(), archiveAnalysis.job?.status === "waiting_retry" || archiveAnalysis.organization?.status === "waiting_retry" ? 30000 : 1500);
    };
    timer = window.setTimeout(() => void poll(), job?.status === "waiting_retry" || organization?.status === "waiting_retry" ? 30000 : 1500);
    return () => { active = false; window.clearTimeout(timer); };
  }, [page, selectedArchive?.session.id, archiveAnalysis.job?.id, archiveAnalysis.job?.status, archiveAnalysis.organization?.status]);
  useEffect(() => {
    if (page !== "archives" || !archiveQuery.trim()) { setArchiveHits([]); return; }
    const timer = window.setTimeout(() => {
      api<(Message & { session_id: string; title: string; source_platform: string })[]>(`/api/archives/search?q=${encodeURIComponent(archiveQuery)}&limit=20`)
        .then(setArchiveHits).catch((e) => setError(e.message));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [page, archiveQuery]);
  async function newSession() {
    if (creatingSessionRef.current) return;
    creatingSessionRef.current = true;
    setSessionCreationPending(true);
    try {
      const item = await api<Session>("/api/sessions", { method: "POST", body: JSON.stringify({ title: "新的对话" }) });
      setSessions((current) => [item, ...current.filter((session) => session.id !== item.id)]); setSessionId(item.id); setMessages([]); setMemorySuggestions([]); setPage("chat");
    } catch (e) { setError((e as Error).message); }
    finally { creatingSessionRef.current = false; setSessionCreationPending(false); }
  }
  async function changeSessionMode(mode: Session["mode"]) {
    if (!sessionId || mode === sessions.find((session) => session.id === sessionId)?.mode) return;
    try {
      const updated = await api<Session>(`/api/sessions/${encodeURIComponent(sessionId)}/mode`, {
        method: "PATCH", body: JSON.stringify({ mode }),
      });
      setSessions((current) => current.map((session) => session.id === updated.id ? { ...session, mode: updated.mode, updated_at: updated.updated_at } : session));
      setError("");
    } catch (e) { setError((e as Error).message); }
  }
  async function openArchiveSession(id: string, offset = 0) {
    try {
      const [data, analysis] = await Promise.all([
        api<ArchivePage>(`/api/archives/sessions/${id}/messages?limit=100&offset=${offset}`),
        api<ArchiveAnalysis>(`/api/archives/sessions/${id}/analysis`),
      ]);
      setSelectedArchive(data); setArchiveAnalysis(analysis); setShowArchiveActions(false); setEditingArchiveTitle(false);
    } catch (e) { setError((e as Error).message); }
  }
  async function renameArchiveSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedArchive) return;
    const title = archiveTitleDraft.trim();
    if (!title) { setError("会话名称不能为空。"); return; }
    try {
      const updated = await api<ArchiveSession>(`/api/archives/sessions/${encodeURIComponent(selectedArchive.session.id)}/title`, {
        method: "PATCH", body: JSON.stringify({ title }),
      });
      setSelectedArchive((current) => current ? { ...current, session: { ...current.session, ...updated } } : current);
      setArchiveSessions((current) => current.map((item) => item.id === updated.id ? { ...item, ...updated } : item));
      setArchiveHits((current) => current.map((item) => item.session_id === updated.id ? { ...item, title: updated.title } : item));
      setEditingArchiveTitle(false);
      setError("");
      setNotice("会话名称已更新");
      window.setTimeout(() => setNotice(""), 2800);
    } catch (e) { setError((e as Error).message); }
  }
  async function analyzeArchiveSession(item: ArchiveSession) {
    const providerName = settings.provider === "openai-compatible"
      ? settings.model
      : settings.providers.find((provider) => provider.id === settings.provider)?.name || settings.provider;
    const scope = `“${item.title}”的全部 ${item.message_count.toLocaleString()} 条消息将分批发送给 ${providerName} 生成摘要和记忆。`;
    if (!window.confirm(`${scope}\n\n分析结果会保存在本机；历史 AI 回复不会作为用户记忆依据。此操作与聊天时的历史检索开关分开。继续吗？`)) return;
    try {
      const result = await api<ArchiveAnalysis>(`/api/archives/sessions/${encodeURIComponent(item.id)}/analysis`, { method: "POST" });
      setArchiveAnalysis(result); setCandidateEdits({}); setError("");
    } catch (e) { setError((e as Error).message); }
  }
  async function decideMemoryCandidate(candidate: MemoryCandidate, action: "approve" | "reject") {
    try {
      await api(`/api/archives/analysis-candidates/${candidate.id}`, {
        method: "PUT",
        body: JSON.stringify({ action, content: candidateEdits[candidate.id] ?? candidate.content, sensitivity: candidate.sensitivity }),
      });
      if (selectedArchive) await openArchiveSession(selectedArchive.session.id, selectedArchive.offset);
      if (action === "approve") await refreshMemories(memorySearch);
      setNotice(action === "approve" ? "已写入长期记忆" : "已忽略这条候选");
      window.setTimeout(() => setNotice(""), 2800);
    } catch (e) { setError((e as Error).message); }
  }
  async function decideChatMemorySuggestion(item: MemorySuggestion, action: "approve" | "dismiss", edits: Partial<MemorySuggestion> = {}) {
    try {
      if (action === "approve") {
        await api<Memory>(`/api/memory-suggestions/${encodeURIComponent(item.id)}/approve`, {
          method: "POST", body: JSON.stringify(edits),
        });
        await refreshMemories(memorySearch);
        setNotice("已确认并保存到长期记忆");
      } else {
        await api(`/api/memory-suggestions/${encodeURIComponent(item.id)}`, { method: "DELETE" });
        setNotice("已忽略这条记忆建议");
      }
      setMemorySuggestions((current) => current.filter((suggestion) => suggestion.id !== item.id));
      window.setTimeout(() => setNotice(""), 2800);
    } catch (e) { setError((e as Error).message); }
  }
  async function continueArchiveSession(item: ArchiveSession) {
    try {
      await api<Session>(`/api/archives/sessions/${encodeURIComponent(item.id)}/continue`, { method: "POST" });
      await refreshSessions();
      setSessionId(item.id); setPage("chat"); setSelectedArchive(null); setError("");
    } catch (e) { setError((e as Error).message); }
  }
  async function deleteArchive(item: ArchiveImport) {
    if (!window.confirm(`删除档案 ${item.source_platform || item.archive_id}？导入的原始消息会删除；已经续聊的会话会保留新增内容并转为普通会话。`)) return;
    try {
      await api(`/api/archives/${encodeURIComponent(item.archive_id)}`, { method: "DELETE" });
      if (selectedArchive?.session.import_id === item.archive_id) setSelectedArchive(null);
      await refreshSessions();
      await refreshArchiveList(archiveQuery, archiveOffset);
    } catch (e) { setError((e as Error).message); }
  }
  async function deleteArchiveSession(item: ArchiveSession) {
    if (!window.confirm(`删除会话《${item.title}》？这会删除导入的消息、图片和在 Agent 中续聊的新消息，且无法恢复。`)) return;
    try {
      await api(`/api/archives/sessions/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      setSelectedArchive(null);
      setArchiveAnalysis({ job: null, candidates: [] });
      const nextOffset = archiveSessions.length === 1 && archiveOffset > 0 ? Math.max(0, archiveOffset - 50) : archiveOffset;
      setArchiveOffset(nextOffset);
      await refreshArchiveList(archiveQuery, nextOffset);
      const remainingSessions = await api<Session[]>("/api/sessions");
      setSessions(remainingSessions);
      if (sessionId === item.id && remainingSessions[0]) setSessionId(remainingSessions[0].id);
      setNotice("会话已删除");
      window.setTimeout(() => setNotice(""), 2800);
    } catch (e) { setError((e as Error).message); }
  }
  async function previewNextArchive() {
    while (archiveFileQueueRef.current.length) {
      const file = archiveFileQueueRef.current.shift()!;
      setArchiveBusy(true); setError("");
      try {
        const response = await fetch("/api/archives/preview", {
          method: "POST", body: file,
          headers: { "Content-Type": file.name.toLowerCase().endsWith(".zip") ? "application/zip" : "application/x-ndjson", "X-Filename": encodeURIComponent(file.name) },
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(`${file.name}：${body.detail || `预览失败（${response.status}）`}`);
        const preview = body as ArchivePreview;
        setArchivePreview({ ...preview, file });
        setArchiveSelection(Object.fromEntries(preview.conversations.map((item) => [item.id, true])));
        setArchivePreviewQuery(""); setArchivePreviewPage(0);
        setArchiveBusy(false);
        return;
      } catch (e) {
        archiveReportsRef.current.push(`${file.name}：预览失败，${(e as Error).message}`);
        setArchiveImportReport(archiveReportsRef.current.join("\n"));
        setError((e as Error).message);
      }
    }
    setArchiveBusy(false);
    await refreshArchiveList(archiveQuery, archiveOffset).catch(() => undefined);
  }
  function previewArchiveFiles(files: FileList | null) {
    if (!files?.length) return;
    archiveFileQueueRef.current = Array.from(files);
    archiveReportsRef.current = [];
    setArchiveImportReport(""); setArchivePreview(null); setError("");
    void previewNextArchive();
    if (archiveFileRef.current) archiveFileRef.current.value = "";
  }
  async function confirmArchiveImport() {
    if (!archivePreview || archiveBusy) return;
    const selected = archivePreview.conversations.filter((item) => archiveSelection[item.id]).map((item) => item.id);
    if (!selected.length) { setError("请至少选择一个会话导入。"); return; }
    setArchiveBusy(true); setError("");
    try {
      const result = await api<{ messages: number; summaries: number; attachments: number }>("/api/archives/import-selected", {
        method: "POST",
        body: JSON.stringify({ token: archivePreview.token, selected_conversation_ids: selected }),
      });
      archiveReportsRef.current.push(`${archivePreview.file.name}：导入 ${selected.length}/${archivePreview.conversations.length} 个会话，${result.messages} 条消息、${result.summaries} 条摘要、${result.attachments} 张图片`);
      setArchiveImportReport(archiveReportsRef.current.join("\n"));
      setArchivePreview(null); setArchiveSelection({});
      await previewNextArchive();
    } catch (e) { setError((e as Error).message); setArchiveBusy(false); }
  }
  async function skipArchivePreview(cancelQueue = false) {
    if (archivePreview) {
      await api(`/api/archives/preview/${encodeURIComponent(archivePreview.token)}`, { method: "DELETE" }).catch(() => undefined);
      archiveReportsRef.current.push(`${archivePreview.file.name}：已跳过`);
      setArchiveImportReport(archiveReportsRef.current.join("\n"));
    }
    setArchivePreview(null); setArchiveSelection({});
    if (cancelQueue) archiveFileQueueRef.current = [];
    await previewNextArchive();
  }
  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || busy || !sessionId) return;
    setDraft(""); setError(""); setBusy(true);
    const optimistic: Message = { id: crypto.randomUUID(), role: "user", content, created_at: new Date().toISOString() };
    const optimisticAssistant: Message = {
      id: crypto.randomUUID(), role: "assistant", content: "", created_at: new Date().toISOString(),
    };
    setStreamingMessageId(optimisticAssistant.id);
    setMessages((current) => [...current, optimistic, optimisticAssistant]);
    try {
      let sensitiveApprovals: Record<string, string> = {};
      if (!/^\s*(?:请)?记住/.test(content)) {
        const preview = await api<{ memories: { id: string; content: string; fingerprint: string }[] }>("/api/chat/memory-preview", {
          method: "POST", body: JSON.stringify({ session_id: sessionId, content }),
        });
        if (preview.memories.length) {
          const details = preview.memories.map((memory, index) => `${index + 1}. ${memory.content}`).join("\n");
          const approved = window.confirm(`本轮可能用到以下敏感记忆：\n\n${details}\n\n确定：仅本次请求附加这些记忆。\n取消：继续发送消息，但不附加敏感记忆。`);
          if (approved) sensitiveApprovals = Object.fromEntries(preview.memories.map((memory) => [memory.id, memory.fingerprint]));
        }
      }
      const response = await fetch("/api/chat", {
        method: "POST", body: JSON.stringify({ session_id: sessionId, content, sensitive_approvals: sensitiveApprovals }),
        headers: { "Content-Type": "application/json" },
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        throw new Error(failure.detail || `请求失败（${response.status}）`);
      }
      if (response.headers.get("content-type")?.includes("text/event-stream")) {
        if (!response.body) throw new Error("浏览器没有收到 Codex 响应流。");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let complete = false;
        const applyBlock = (block: string) => {
          const line = block.split("\n").find((part) => part.startsWith("data: "));
          if (!line) return;
          const item = JSON.parse(line.slice(6)) as { type: string; content?: string; message?: string; user_message?: Message; assistant_message?: Message; memory_suggestions?: MemorySuggestion[] };
          if (item.type === "delta" && item.content) {
            setMessages((current) => current.map((message) => message.id === optimisticAssistant?.id
              ? { ...message, content: message.content + item.content }
              : message));
          } else if (item.type === "done" && item.user_message && item.assistant_message) {
            complete = true;
            pendingAssistantReplacementRef.current = {
              localId: optimisticAssistant.id,
              message: item.assistant_message,
            };
            setMessages((current) => current.map((message) => {
              if (message.id === optimistic.id) return item.user_message!;
              if (message.id === optimisticAssistant.id) return { ...item.assistant_message!, id: optimisticAssistant.id };
              return message;
            }));
            setAnimatedMessageId(optimisticAssistant.id);
            setMemorySuggestions((current) => mergeMemorySuggestions(current, item.memory_suggestions || []));
          } else if (item.type === "error") {
            throw new Error(item.message || "Codex 处理消息失败。");
          }
        };
        while (true) {
          const { value, done } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            applyBlock(buffer.slice(0, boundary));
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf("\n\n");
          }
          if (done) break;
        }
        if (buffer.trim()) applyBlock(buffer);
        if (!complete) throw new Error("Codex 响应流提前结束了。");
      } else {
        const result = await response.json() as { user_message: Message; assistant_message: Message; memory_suggestions?: MemorySuggestion[] };
        setMessages((current) => [...current.filter((message) => message.id !== optimistic.id && message.id !== optimisticAssistant.id), result.user_message, result.assistant_message]);
        setAnimatedMessageId(result.assistant_message.id);
        setMemorySuggestions((current) => mergeMemorySuggestions(current, result.memory_suggestions || []));
      }
      await refreshSessions();
      if (/^\s*(?:请)?记住/.test(content)) { setNotice("已按你的明确要求保存到记忆"); window.setTimeout(() => setNotice(""), 3200); }
    } catch (e) {
      if (pendingAssistantReplacementRef.current?.localId === optimisticAssistant.id) pendingAssistantReplacementRef.current = null;
      setMessages((current) => current.filter((message) => message.id !== optimisticAssistant.id));
      setError((e as Error).message);
    } finally { setBusy(false); setStreamingMessageId(null); }
  }
  function finishAssistantReveal(messageId: string) {
    setAnimatedMessageId((current) => current === messageId ? null : current);
    const pending = pendingAssistantReplacementRef.current;
    if (!pending || pending.localId !== messageId) return;
    pendingAssistantReplacementRef.current = null;
    setMessages((current) => current.map((message) => message.id === messageId ? pending.message : message));
  }
  async function saveSettings(event: FormEvent) {
    event.preventDefault(); setError(""); setNotice("");
    try {
      const next = await api<Settings>("/api/settings", {
        method: "PUT", body: JSON.stringify({ ...settingsDraft, api_key: apiKey || null }),
      });
      setSettings(next); setSettingsDraft(next); setApiKey(""); setNotice(next.provider === "codex-app-server" ? "已切换到 Codex Agent" : "设置已保存，API Key 已交由系统凭据存储保管");
      window.setTimeout(() => setNotice(""), 4000);
    } catch (e) { setError((e as Error).message); }
  }
  async function changeDefaultMode(mode: Session["mode"]) {
    try {
      const next = await api<Settings>("/api/settings", {
        method: "PUT", body: JSON.stringify({ ...settings, default_mode: mode, api_key: null }),
      });
      setSettings(next); setSettingsDraft((current) => ({ ...current, default_mode: next.default_mode }));
      setNotice("新对话的默认模式已更新"); window.setTimeout(() => setNotice(""), 2800);
    } catch (e) { setError((e as Error).message); }
  }
  async function removeKey() {
    try { await api("/api/settings/api-key", { method: "DELETE" }); setSettings((s) => ({ ...s, has_api_key: false })); setSettingsDraft((s) => ({ ...s, has_api_key: false })); }
    catch (e) { setError((e as Error).message); }
  }
  async function saveMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const payload = {
      content: String(data.get("content") || "").trim(),
      memory_type: String(data.get("memory_type") || "semantic"),
      importance: Number(data.get("importance") || 3),
      sensitivity: String(data.get("sensitivity") || "normal"),
    };
    try {
      if (editingMemory && editingMemory !== "new") await api(`/api/memories/${editingMemory.id}`, { method: "PUT", body: JSON.stringify(payload) });
      else await api("/api/memories", { method: "POST", body: JSON.stringify(payload) });
      setEditingMemory(null); await refreshMemories(memorySearch); setNotice("记忆已保存"); window.setTimeout(() => setNotice(""), 2800);
    } catch (e) { setError((e as Error).message); }
  }
  async function deleteMemory(item: Memory) {
    if (!window.confirm("删除这条记忆？删除后无法恢复。")) return;
    try { await api(`/api/memories/${item.id}`, { method: "DELETE" }); await refreshMemories(memorySearch); }
    catch (e) { setError((e as Error).message); }
  }
  const activeMode = sessions.find((session) => session.id === sessionId)?.mode ?? settings.default_mode;
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">拾</span><div><strong>拾光</strong><small>个人生活 Agent</small></div></div>
      <button className={`nav-item ${page === "chat" ? "active" : ""}`} onClick={() => setPage("chat")}><span>◌</span> 对话</button>
      <button className={`nav-item ${page === "archives" ? "active" : ""}`} onClick={() => setPage("archives")}><span>⌕</span> 历史档案 <span className="nav-count">{archiveTotal || ""}</span></button>
      <button className={`nav-item ${page === "memories" ? "active" : ""}`} onClick={() => setPage("memories")}><span>✳</span> 记忆 <span className="nav-count">{memories.length || ""}</span></button>
      <button className={`nav-item ${page === "settings" ? "active" : ""}`} onClick={() => setPage("settings")}><span>⚙</span> 模型设置</button>
      <div className="sidebar-divider" />
      <div className="section-heading"><span>最近对话</span><button aria-label="新建对话" title="新建对话" onClick={newSession} disabled={sessionCreationPending}>＋</button></div>
      <div className="session-list">{sessions.map((item) => <button key={item.id} className={`session-item ${sessionId === item.id && page === "chat" ? "selected" : ""}`} onClick={() => { setSessionId(item.id); setPage("chat"); if (sessionId === item.id && page === "chat") void chatSync.refresh().catch((e) => setError(e.message)); }}><span className="session-dot" />{item.title}{item.origin === "continued" && <small className="session-origin">续聊</small>}</button>)}</div>
      <div className="sidebar-bottom"><span className="local-dot" />仅在本机运行 <span className="bottom-lock">⌑</span></div>
    </aside>
    <main className="main-area">
      <header className="topbar"><div className="breadcrumb">{page === "chat" ? "日常陪伴" : page === "archives" ? "历史档案" : page === "memories" ? "我的记忆" : page === "model-logs" ? "模型调用记录" : "偏好设置"}</div><div className="top-actions"><div className="theme-picker-wrap" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setShowThemeMenu(false); }}><button className="theme-trigger" aria-expanded={showThemeMenu} onClick={() => setShowThemeMenu((open) => !open)}><span className={`theme-swatch swatch-${theme}`} />外观<span className="theme-chevron">⌄</span></button>{showThemeMenu && <div className="theme-menu" role="menu"><div className="theme-menu-title">页面风格</div>{([{ id: "warm", label: "暖纸", detail: "柔和、安静" }, { id: "mist", label: "浅青", detail: "清爽、轻盈" }, { id: "night", label: "夜间", detail: "低亮度、舒适" }] as const).map((option) => <button key={option.id} role="menuitemradio" aria-checked={theme === option.id} className={`theme-option ${theme === option.id ? "selected" : ""}`} onClick={() => { setTheme(option.id); setShowThemeMenu(false); }}><span className={`theme-swatch swatch-${option.id}`} /><span className="theme-option-copy"><strong>{option.label}</strong><small>{option.detail}</small></span><span className="theme-check">{theme === option.id ? "✓" : ""}</span></button>)}</div>}</div><div className="top-status"><span className="local-dot" /> 本机模式</div></div></header>
      {error && <div className="toast error-toast"><span>!</span>{error}<button onClick={() => setError("")}>×</button></div>}
      {notice && <div className="toast success-toast"><span>✓</span>{notice}<button onClick={() => setNotice("")}>×</button></div>}
      {page === "chat" && <section className="chat-page">
        <div className="chat-heading"><div><span className="eyebrow">PERSONAL COMPANION</span><h1>今天，想从哪里聊起？</h1><p>{sessions.find((item) => item.id === sessionId)?.origin === "continued" ? "正在接续一段导入的历史会话；这里展示最近 100 条，完整记录可在历史档案中查看。" : "我会认真听，也会尊重你希望保留的边界。"}</p></div><button className="new-chat-button" onClick={newSession} disabled={sessionCreationPending}>＋ <span>新对话</span></button></div>
        {chatSync.status === "processing" && <div className="chat-sync-note">正在等待模型回复；离开页面或刷新后会自动同步。</div>}
        {chatSync.status === "interrupted" && <div className="chat-sync-note interrupted">{chatSync.statusError || "上一条消息没有收到回复。你的消息仍保留在会话中。"}</div>}
        {messages.length === 0 ? <div className="welcome-card"><div className="sun-icon">☼</div><h2>这里是你的私人空间</h2><p>你可以聊聊今天，也可以一起整理一个念头。<br />发现可能有帮助的内容时，我会先征求你的确认。</p><div className="prompt-chips"><button onClick={() => setDraft("今天有件事想和你说说。")}>聊聊今天发生的事 <span>↗</span></button><button onClick={() => setDraft("我想理一理最近的感受。")}>整理最近的感受 <span>↗</span></button></div></div> : <div className="message-list">{messages.map((message) => <article key={message.id} className={`message ${message.role}`}><div className="avatar">{message.role === "assistant" ? "拾" : "我"}</div><div className="message-body"><div className="message-label">{message.origin === "imported" ? (message.speaker_label || message.speaker || message.role) : message.role === "assistant" ? "拾光" : "你"}{message.origin === "imported" && <small className="message-origin">历史导入</small>}{message.origin === "continued" && <small className="message-origin">续聊</small>}</div>{message.role === "assistant" ? <AssistantReply content={message.content} waiting={message.id === streamingMessageId && !message.content} streaming={message.id === streamingMessageId && Boolean(message.content)} revealing={message.id === animatedMessageId} onRevealComplete={() => finishAssistantReveal(message.id)} /> : <div className="message-text">{message.content}</div>}<ImageAttachments items={message.attachments} /></div></article>)}<div ref={endRef} /></div>}
        <MemorySuggestions items={memorySuggestions} onApprove={(item, edits) => void decideChatMemorySuggestion(item, "approve", edits)} onDismiss={(item) => void decideChatMemorySuggestion(item, "dismiss")} />
        <div className="composer-wrap"><div className="mode-switch"><div className="mode-switch-copy"><strong>{activeMode === "chat" ? "聊天模式" : "管家模式"}</strong><small>{activeMode === "chat" ? "倾听交流，按需给建议" : "规划安排，评估风险并跟进"}</small></div><div className="mode-switch-options" role="group" aria-label="对话模式"><button type="button" className={activeMode === "chat" ? "selected" : ""} aria-pressed={activeMode === "chat"} onClick={() => void changeSessionMode("chat")}>聊天</button><button type="button" className={activeMode === "manager" ? "selected" : ""} aria-pressed={activeMode === "manager"} onClick={() => void changeSessionMode("manager")}>管家</button></div></div><form className="composer" onSubmit={sendMessage}><textarea value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} placeholder="写下你此刻想说的话…" rows={2} /><div className="composer-footer"><span><kbd>Enter</kbd> 发送 · <kbd>Shift + Enter</kbd> 换行</span><button className="send-button" disabled={!draft.trim() || busy} aria-label="发送">↑</button></div></form><div className="privacy-hint">{settings.provider !== "openai-compatible" ? `消息将由本机 ${settings.providers.find((provider) => provider.id === settings.provider)?.name || "CLI Agent"} 处理` : settings.has_api_key ? "消息将发送至你配置的模型服务" : "先在模型设置中配置服务，或直接说「记住这个：…」保存一条记忆"}{settings.use_memories_in_cloud && (settings.has_api_key || settings.has_codex_login) ? " · 相关记忆已开启注入" : ""}{settings.use_archive_history ? " · 已开启历史档案检索，相关原文会附加到模型请求" : ""}</div></div>
      </section>}
      {page === "archives" && <section className="content-page archive-page"><div className="page-title-row"><div><span className="eyebrow">PORTABLE CONVERSATION HISTORY</span><h1>历史档案</h1><p>导入标准会话档案，在本机搜索和回看原始消息。</p></div><div className="archive-links"><a href="/api/archives/format">格式说明</a><a href="/api/archives/example">示例文件</a><a href="/api/archives/conversion-prompt">AI 转换指令</a></div></div>
        <div className="archive-upload-card"><div><strong>导入标准档案</strong><p>支持 JSONL/NDJSON 或含多个分片的 ZIP。单个分片不超过 100 MiB；ZIP 上传不超过 100 MiB，解压后不超过 500 MiB；每个档案最多 100,000 条消息。</p><small>转换时请按「AI 转换指令」和「示例文件」核对：每条 user/assistant 发言独立成条，时间写入对应消息的 timestamp。导入器只校验格式，不会修复角色或时间错位。</small></div><input ref={archiveFileRef} className="hidden-file-input" type="file" accept=".jsonl,.ndjson,.zip" multiple onChange={(event) => previewArchiveFiles(event.currentTarget.files)} /><button className="primary-button" disabled={archiveBusy || Boolean(archivePreview)} onClick={() => archiveFileRef.current?.click()}>{archiveBusy ? "正在检查…" : archivePreview ? "正在选择会话…" : "选择档案文件"}</button></div>
        {archiveImportReport && <pre className="archive-report">{archiveImportReport}</pre>}
        {archivePreview && <section className="archive-preview"><div className="archive-preview-heading"><div><strong>导入前选择会话</strong><small>{archivePreview.file.name} · {archivePreview.source_platform || "通用档案"} · {archivePreview.expected_parts} 个分片 · {archivePreview.image_count} 张图片 · {formatBytes(archivePreview.image_bytes)}</small></div><div><button onClick={() => setArchiveSelection(Object.fromEntries(archivePreview.conversations.map((item) => [item.id, true])))}>全选</button><button onClick={() => setArchiveSelection({})}>全不选</button></div></div><div className="archive-preview-toolbar"><input value={archivePreviewQuery} onChange={(event) => { setArchivePreviewQuery(event.target.value); setArchivePreviewPage(0); }} placeholder="按会话标题筛选" /><span>{filteredArchiveConversations.length.toLocaleString()} 个会话</span></div><div className="archive-preview-list">{filteredArchiveConversations.slice(archivePreviewPage * 100, (archivePreviewPage + 1) * 100).map((item) => <label className="archive-preview-item" key={item.id}><input type="checkbox" checked={Boolean(archiveSelection[item.id])} onChange={(event) => { const checked = event.currentTarget.checked; setArchiveSelection((current) => ({ ...current, [item.id]: checked })); }} /><span><strong>{item.title || "未命名会话"}</strong><small>{item.created_at ? new Date(item.created_at).toLocaleDateString("zh-CN") : "时间未知"} · {item.message_count.toLocaleString()} 条消息{item.image_count > 0 && <> · {item.image_count} 张图片 · {formatBytes(item.image_bytes)}</>}</small></span>{item.preview_images.length > 0 && <span className="archive-preview-images">{item.preview_images.map((image) => <img key={image.id} src={`/api/archives/preview/${encodeURIComponent(archivePreview.token)}/images/${encodeURIComponent(image.id)}`} alt={image.filename} loading="lazy" />)}{item.image_count > item.preview_images.length && <small>+{item.image_count - item.preview_images.length}</small>}</span>}</label>)}</div>{!filteredArchiveConversations.slice(archivePreviewPage * 100, (archivePreviewPage + 1) * 100).length && <p className="archive-preview-no-results">没有匹配的会话。</p>}<div className="archive-preview-footer"><span>已选 {archivePreview.conversations.filter((item) => archiveSelection[item.id]).length.toLocaleString()} / {archivePreview.conversations.length.toLocaleString()} 个会话</span><div className="archive-preview-controls"><button disabled={archivePreviewPage === 0} onClick={() => setArchivePreviewPage((page) => Math.max(0, page - 1))}>上一页</button><small>{archivePreviewPage + 1} / {Math.max(1, Math.ceil(filteredArchiveConversations.length / 100))}</small><button disabled={(archivePreviewPage + 1) * 100 >= filteredArchiveConversations.length} onClick={() => setArchivePreviewPage((page) => page + 1)}>下一页</button></div><div><button disabled={archiveBusy} onClick={() => void skipArchivePreview()}>跳过此文件</button><button disabled={archiveBusy} onClick={() => void skipArchivePreview(true)}>取消剩余文件</button><button className="primary-button" disabled={archiveBusy || !archivePreview.conversations.some((item) => archiveSelection[item.id])} onClick={() => void confirmArchiveImport()}>导入已选会话</button></div></div></section>}
        <div className="archive-imports"><strong>已导入档案</strong>{archiveImports.length ? archiveImports.map((item) => <div className="archive-import-row" key={item.archive_id}><span><b>{item.source_platform || "通用档案"}</b><small>{item.archive_id}</small></span><span>{item.message_count.toLocaleString()} 条消息 · {item.image_count || 0} 张图片 · {item.imported_parts}/{item.expected_parts} 个分片</span><button className="archive-delete" onClick={() => void deleteArchive(item)}>删除</button></div>) : <p>还没有导入档案。</p>}</div>
        <div className="archive-toolbar"><label className="searchbox"><span>⌕</span><input value={archiveQuery} onChange={(event) => { setArchiveQuery(event.target.value); setArchiveOffset(0); }} placeholder="搜索会话标题和原始消息" /></label></div>
        <div className="archive-layout"><div className="archive-session-list"><div className="archive-list-heading">会话 <span>{archiveTotal.toLocaleString()}</span></div>{archiveSessions.map((item) => <button className={`archive-session-row ${selectedArchive?.session.id === item.id ? "selected" : ""}`} key={item.id} onClick={() => void openArchiveSession(item.id)}><strong>{item.title}</strong><small>{item.source_platform || "导入档案"} · {item.message_count.toLocaleString()} 条消息</small></button>)}{!archiveSessions.length && <div className="archive-empty">{archiveQuery ? "标题中没有匹配的会话；可查看右侧消息搜索结果。" : "导入后会在这里显示会话。"}</div>}{archiveTotal > 50 && <div className="archive-pager"><button disabled={archiveOffset === 0} onClick={() => setArchiveOffset(Math.max(0, archiveOffset - 50))}>上一页</button><button disabled={archiveOffset + 50 >= archiveTotal} onClick={() => setArchiveOffset(archiveOffset + 50)}>下一页</button></div>}</div>
          <div className="archive-detail">{archiveQuery.trim() && <div className="archive-hits"><div className="archive-list-heading">消息匹配 <span>{archiveHits.length}</span></div>{archiveHits.map((item) => <button key={item.id} className="archive-hit" onClick={() => void openArchiveSession(item.session_id, Math.floor(((item.sequence || 1) - 1) / 100) * 100)}><strong>{item.title} · #{item.sequence}</strong><small>{item.source_platform || "导入档案"} · {item.source_timestamp || "时间未知"} · {item.speaker_label || item.speaker}</small><span>{item.content.slice(0, 320)}{item.content.length > 320 ? "…" : ""}</span></button>)}</div>}
            {selectedArchive ? <>
              <div className="archive-detail-heading"><div><div className="archive-title-row">{editingArchiveTitle ? <form className="archive-title-edit" onSubmit={(event) => void renameArchiveSession(event)}><input autoFocus aria-label="会话名称" maxLength={80} value={archiveTitleDraft} onChange={(event) => setArchiveTitleDraft(event.target.value)} /><button className="primary-button" type="submit">保存</button><button type="button" onClick={() => setEditingArchiveTitle(false)}>取消</button></form> : <strong>{selectedArchive.session.title}</strong>}</div><small>{selectedArchive.session.source_platform || "导入档案"} · {selectedArchive.total.toLocaleString()} 条消息{selectedArchive.session.origin === "continued" ? " · 已续聊" : ""}</small></div><div className="archive-detail-actions">
                {selectedArchive.session.continuable && <>{(!archiveAnalysis.job || archiveAnalysis.job.status !== "failed" || archiveAnalysis.job.requires_user_action) ? <button className="primary-button" onClick={() => void analyzeArchiveSession(selectedArchive.session)} disabled={archiveAnalysisRunning || archiveAnalysis.candidates.some((candidate) => candidate.status === "pending") && archiveAnalysis.job?.status !== "failed"}>{archiveAnalysis.job?.status === "complete" ? "重新分析" : archiveAnalysis.job?.status === "failed" ? "解决问题后继续" : "生成摘要和记忆"}</button> : <small className="analysis-progress-note">应用已自动重试；当前问题未恢复，请查看错误信息。</small>}<button className="primary-button" onClick={() => void continueArchiveSession(selectedArchive.session)} disabled={archiveAnalysisRunning} title={archiveAnalysisRunning ? "分析完成后即可继续此会话" : undefined}>继续此会话</button></>}
                <div className="archive-more-wrap" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setShowArchiveActions(false); }}><button className="archive-more-button" aria-label="更多会话操作" aria-haspopup="menu" aria-expanded={showArchiveActions} onClick={() => setShowArchiveActions((open) => !open)}>⋯</button>{showArchiveActions && <div className="archive-more-menu" role="menu"><button role="menuitem" onClick={() => { setArchiveTitleDraft(selectedArchive.session.title); setEditingArchiveTitle(true); setShowArchiveActions(false); }}>修改名称</button><button role="menuitem" className="danger" onClick={() => { setShowArchiveActions(false); void deleteArchiveSession(selectedArchive.session); }}>删除会话</button><button role="menuitem" onClick={() => { setShowArchiveActions(false); setSelectedArchive(null); }}>收起详情</button></div>}</div>
              </div></div>
              {selectedArchive.summaries.length > 0 && <div className="archive-summary-list"><strong>随档案导入的摘要（独立于原始消息）</strong>{selectedArchive.summaries.map((item) => <article key={item.source_summary_id}><p>{item.content}</p><small>依据消息 ID：{item.based_on_message_ids || "未注明"}</small></article>)}</div>}
              {archiveAnalysis.job && <section className="analysis-panel">
                <div className="analysis-heading"><strong>会话分析</strong><span>{({ queued: "等待中", processing: "分析中", waiting_retry: "等待额度恢复", complete: "完成", failed: "失败" })[archiveAnalysis.job.status]} · {Math.round(archiveAnalysis.job.progress)}%</span></div>
                <div className="analysis-progress"><i style={{ width: `${Math.max(2, archiveAnalysis.job.progress)}%` }} /></div>
                {["queued", "processing"].includes(archiveAnalysis.job.status) && <p className="analysis-progress-note">已处理 {archiveAnalysis.job.processed_chars.toLocaleString("zh-CN")} / {archiveAnalysis.job.total_chars.toLocaleString("zh-CN")} 字符。大档案会分批分析，每批完成后进度才会推进；可以稍后回来查看，无需重复点击。</p>}
                {archiveAnalysis.job.provider && <small className="analysis-provider">使用：{archiveAnalysis.job.provider}</small>}
                {archiveAnalysis.job.status === "waiting_retry" ? <p className="analysis-progress-note">已达到模型额度，预计 {archiveAnalysis.job.retry_after ? new Date(archiveAnalysis.job.retry_after).toLocaleString("zh-CN", { hour12: false }) : "约 5 小时后"} 自动从已完成进度继续。应用重启后也会接着等待。</p> : archiveAnalysis.job.error && <p className="analysis-error">{archiveAnalysis.job.error}</p>}
                {archiveAnalysis.job.summary && <details className="generated-summary"><summary>查看生成的会话摘要</summary><p>{archiveAnalysis.job.summary}</p></details>}
                {archiveAnalysis.job.status === "complete" && <ArchiveCandidateOrganization analysis={archiveAnalysis} onRefresh={async () => { if (!selectedArchive) return; setArchiveAnalysis(await api<ArchiveAnalysis>(`/api/archives/sessions/${encodeURIComponent(selectedArchive.session.id)}/analysis`)); }} onOpenReference={(id, sequence) => void openArchiveSession(id, Math.floor((sequence - 1) / 100) * 100)} />}
                {archiveAnalysis.candidates.length > 0 && !["complete", "queued", "processing", "waiting_retry"].includes(archiveAnalysis.organization?.status || "") && <OriginalCandidates candidates={archiveAnalysis.candidates} edits={candidateEdits} onEdit={(id, content) => setCandidateEdits((current) => ({ ...current, [id]: content }))} onSensitivity={(id, sensitivity) => setArchiveAnalysis((current) => ({ ...current, candidates: current.candidates.map((item) => item.id === id ? { ...item, sensitivity } : item) }))} onDecision={(candidate, action) => void decideMemoryCandidate(candidate, action)} onOpenReference={(id, sequence) => void openArchiveSession(id, Math.floor((sequence - 1) / 100) * 100)} />}
              </section>}
              <div className="archive-message-list">{selectedArchive.items.map((item) => <article className="archive-message" key={item.id}><div><strong>{item.speaker_label || item.speaker || item.role}</strong><small>{item.source_timestamp || "时间未知"} · #{item.sequence}</small></div><p>{item.content}</p><ImageAttachments items={item.attachments} /></article>)}</div>
              <div className="archive-pager"><button disabled={selectedArchive.offset === 0} onClick={() => void openArchiveSession(selectedArchive.session.id, Math.max(0, selectedArchive.offset - selectedArchive.limit))}>上一页</button><span>{selectedArchive.offset + 1}–{Math.min(selectedArchive.offset + selectedArchive.items.length, selectedArchive.total)} / {selectedArchive.total}</span><button disabled={selectedArchive.offset + selectedArchive.limit >= selectedArchive.total} onClick={() => void openArchiveSession(selectedArchive.session.id, selectedArchive.offset + selectedArchive.limit)}>下一页</button></div>
            </> : !archiveQuery.trim() && <div className="archive-empty archive-detail-empty">选择一个历史会话，或搜索消息内容。</div>}
          </div>
        </div>
      </section>}
      {page === "memories" && <section className="content-page"><div className="page-title-row"><div><span className="eyebrow">YOUR PERSONAL CONTEXT</span><h1>我的记忆</h1><p>由你决定留下什么。所有记忆都可以随时修改或删除。</p></div><button className="primary-button" onClick={() => setEditingMemory("new")}>＋ 新增记忆</button></div><div className="memory-toolbar"><label className="searchbox"><span>⌕</span><input value={memorySearch} onChange={(e) => setMemorySearch(e.target.value)} placeholder="搜索记忆内容" /></label><span className="result-count">{memories.filter((item) => memoryTypeFilter === "all" || item.memory_type === memoryTypeFilter).length} 条记忆</span></div><div className="memory-type-filters">{([{ id: "all", text: "全部" }, { id: "working", text: labels.working }, { id: "episodic", text: labels.episodic }, { id: "semantic", text: labels.semantic }] as const).map((item) => <button key={item.id} className={memoryTypeFilter === item.id ? "selected" : ""} onClick={() => setMemoryTypeFilter(item.id)}>{item.text}</button>)}</div><div className="memory-grid">{memories.filter((item) => memoryTypeFilter === "all" || item.memory_type === memoryTypeFilter).map((item) => <article className="memory-card" key={item.id}><div className="memory-card-top"><span className={`memory-type type-${item.memory_type}`}>{labels[item.memory_type]}</span><span className="memory-date">{new Date(item.updated_at).toLocaleDateString("zh-CN")}</span></div><p>{item.content}</p>{item.source === "导入档案审核采纳" && <small className="memory-source">来自导入会话分析 · {(() => { try { const refs = JSON.parse(item.source_refs || "[]") as AnalysisReference[]; const preview = refs.slice(0, 3).map((ref) => `《${ref.session_title || "历史会话"}》#${ref.sequence}`).join("、"); return preview + (refs.length > 3 ? ` 等 ${refs.length} 处依据` : "") + (refs.some((ref) => ref.source_deleted) ? "（来源档案已删除）" : ""); } catch { return "原文依据"; } })()}</small>}<div className="memory-card-bottom"><span>{item.sensitivity === "private" ? "⊘ 严格私密" : item.sensitivity === "sensitive" ? "◉ 敏感" : `重要度 ${item.importance}/5`}</span><div><button onClick={() => setEditingMemory(item)}>编辑</button><button className="delete-button" onClick={() => void deleteMemory(item)}>删除</button></div></div></article>)}{!memories.some((item) => memoryTypeFilter === "all" || item.memory_type === memoryTypeFilter) && <div className="empty-state"><div>✳</div><h3>{memorySearch ? "没有找到相关记忆" : "这里还没有记忆"}</h3><p>{memorySearch ? "试试其他关键词。" : "你可以手动添加，或在对话中说「记住这个：…」。"}</p></div>}</div></section>}
      {page === "model-logs" && <ModelCallLogs onBack={() => setPage("settings")} />}
      {page === "settings" && <section className="content-page settings-page"><div className="page-title-row"><div><span className="eyebrow">MODEL & PRIVACY</span><h1>模型设置</h1><p>模型可以更换，你的本地记忆会继续保留。</p></div><div className="secure-badge"><span>⌑</span> 本机凭据</div></div><div className="settings-card default-mode-card"><label className="field"><span>新对话默认模式</span><select value={settings.default_mode} onChange={(event) => void changeDefaultMode(event.target.value as Session["mode"])}><option value="chat">聊天模式</option><option value="manager">管家模式</option></select><small>只影响新建的对话；当前对话可以单独切换。</small></label></div><form className="settings-card" onSubmit={saveSettings}><div className="card-heading"><div className="card-icon">◈</div><div><h2>运行方式</h2><p>选择 API 服务或已注册的本机 CLI 插件</p></div></div><div className="form-grid"><label className="field full-field"><span>Agent 运行方式</span><select value={settingsDraft.provider} onChange={(e) => setSettingsDraft({ ...settingsDraft, provider: e.target.value })}><option value="openai-compatible">OpenAI 兼容 API</option>{settingsDraft.providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label>{settingsDraft.provider !== "openai-compatible" ? <div className="codex-status full-field"><strong>{(() => { const selected = settingsDraft.providers.find((provider) => provider.id === settingsDraft.provider); return !selected ? "\u672a\u627e\u5230 CLI \u63d2\u4ef6" : !selected.available ? `${selected.name} \u4e0d\u53ef\u7528` : selected.authenticated === false ? `${selected.name} \u5c1a\u672a\u767b\u5f55` : `${selected.name} \u5df2\u5c31\u7eea`; })()}</strong><p>{(() => { const selected = settingsDraft.providers.find((provider) => provider.id === settingsDraft.provider); return selected?.message || selected?.description || "\u8bf7\u5b89\u88c5\u5e76\u6ce8\u518c\u5bf9\u5e94\u7684 CLI \u63d2\u4ef6\u3002"; })()}</p></div> : <><label className="field full-field"><span>API 地址</span><input required value={settingsDraft.base_url} onChange={(e) => setSettingsDraft({ ...settingsDraft, base_url: e.target.value })} placeholder="https://api.example.com/v1" /><small>仅接受 HTTPS 地址；本地模型可使用 localhost</small></label><label className="field full-field"><span>模型名称</span><input required value={settingsDraft.model} onChange={(e) => setSettingsDraft({ ...settingsDraft, model: e.target.value })} placeholder="模型名称" /></label><label className="field full-field"><span>API 协议</span><select value={settingsDraft.api_protocol} onChange={(event) => setSettingsDraft({ ...settingsDraft, api_protocol: event.target.value as Settings["api_protocol"] })}><option value="chat_completions">Chat Completions（本地历史续接）</option><option value="responses">Responses API（服务端会话续接）</option></select><small>只有 Responses API 会保存并续接服务端返回的会话标识；服务端会保存对话内容。</small></label><label className="field full-field"><span>API Key {settings.has_api_key && <em className="key-saved">已安全保存</em>}</span><input type="password" autoComplete="new-password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={settings.has_api_key ? "留空以保留当前密钥" : "粘贴 API Key"} />{settings.has_api_key && <button className="text-button" type="button" onClick={() => void removeKey()}>删除已保存的密钥</button>}</label><label className="field"><span>温度</span><input type="number" min="0" max="2" step="0.1" value={settingsDraft.temperature} onChange={(e) => setSettingsDraft({ ...settingsDraft, temperature: Number(e.target.value) })} /></label><label className="field"><span>最大输出长度</span><input type="number" min="1" max="32000" value={settingsDraft.max_tokens} onChange={(e) => setSettingsDraft({ ...settingsDraft, max_tokens: Number(e.target.value) })} /></label><label className="field"><span>模型上下文窗口</span><input type="number" min="2048" max="1000000" step="1024" value={settingsDraft.context_window_tokens} onChange={(e) => setSettingsDraft({ ...settingsDraft, context_window_tokens: Number(e.target.value) })} /><small>填写模型上限；用于本机估算何时压缩会话</small></label></>}</div><div className="mode-prompt-fields"><label className="field full-field"><span>聊天模式提示语</span><textarea rows={5} maxLength={5000} value={settingsDraft.chat_mode_prompt} onChange={(event) => setSettingsDraft({ ...settingsDraft, chat_mode_prompt: event.target.value })} /><small>定义聊天模式的回应重点。独立判断等共用原则仍会在所有模式中生效。</small></label><label className="field full-field"><span>管家模式提示语</span><textarea rows={5} maxLength={5000} value={settingsDraft.manager_mode_prompt} onChange={(event) => setSettingsDraft({ ...settingsDraft, manager_mode_prompt: event.target.value })} /><small>定义管家模式的规划与跟进方式。保存后会用于该模式下的新请求。</small></label></div><div className="privacy-setting"><div><strong>允许把相关普通长期记忆加入模型请求</strong><p>开启后，每次请求最多附带 4 条相关普通记忆；敏感记忆会在每次发送前单独确认。</p></div><button type="button" role="switch" aria-checked={settingsDraft.use_memories_in_cloud} className={`toggle ${settingsDraft.use_memories_in_cloud ? "on" : ""}`} onClick={() => setSettingsDraft({ ...settingsDraft, use_memories_in_cloud: !settingsDraft.use_memories_in_cloud })}><span /></button></div><MemoryPrivacySettings draft={settingsDraft} onChange={(patch) => setSettingsDraft((current) => ({ ...current, ...patch }))} /><div className="privacy-setting"><div><strong>允许检索历史档案并附加到模型请求</strong><p>开启后，会按当前问题检索最多 5 条历史命中（包括其他会话和已压缩的当前会话原文）；相关文本会随当前请求发送。默认关闭。</p></div><button type="button" role="switch" aria-checked={settingsDraft.use_archive_history} className={`toggle ${settingsDraft.use_archive_history ? "on" : ""}`} onClick={() => setSettingsDraft({ ...settingsDraft, use_archive_history: !settingsDraft.use_archive_history })}><span /></button></div><label className="field full-field agent-definition-field"><span>共用 Agent 设定</span><textarea rows={4} maxLength={5000} value={settingsDraft.agent_definition} onChange={(e) => setSettingsDraft({ ...settingsDraft, agent_definition: e.target.value })} placeholder="例如：保持温和直接；先倾听，再给建议。" /><small>定义两个模式共用的角色和沟通方式；模式提示语可单独编辑。这里的内容与关于你的长期记忆分开保存。</small></label><div className="cloud-note"><span>ⓘ</span><p>当前对话会发送给所选模型。长期记忆和历史档案各自使用独立开关。</p></div><div className="settings-actions"><span>{settingsDraft.provider !== "openai-compatible" ? "沿用所选 CLI 自身的登录状态" : settings.has_api_key ? "密钥存储在 Windows 凭据管理器" : "尚未配置 API Key"}</span><button className="primary-button" type="submit">保存设置</button></div></form><div className="local-storage-note"><span>▣</span><div><strong>本地数据</strong><p>会话和记忆保存在本机应用数据目录中；发送给云端的内容取决于所选运行方式和记忆开关。</p><button className="text-button" onClick={() => setPage("model-logs")}>查看模型调用记录</button></div></div></section>}
    </main>
    {editingMemory !== null && <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setEditingMemory(null); }}><form className="memory-modal" onSubmit={saveMemory}><div className="modal-heading"><div><span className="eyebrow">MEMORY CONTROL</span><h2>{editingMemory === "new" ? "新增一条记忆" : "编辑记忆"}</h2></div><button type="button" className="close-button" onClick={() => setEditingMemory(null)}>×</button></div><p className="modal-description">仅记录你确认过的内容。推测和单次情绪不应变成长期事实。</p><label className="field"><span>记忆内容</span><textarea name="content" required maxLength={5000} defaultValue={editingMemory === "new" ? "" : editingMemory.content} placeholder="写下希望 Agent 记住的内容" rows={4} /></label><div className="form-grid modal-fields"><label className="field"><span>记忆类型</span><select name="memory_type" defaultValue={editingMemory === "new" ? "semantic" : editingMemory.memory_type}><option value="semantic">稳定偏好</option><option value="episodic">重要经历</option><option value="working">短期事项</option></select></label><label className="field"><span>重要度</span><select name="importance" defaultValue={editingMemory === "new" ? 3 : editingMemory.importance}><option value="1">1 · 一般</option><option value="2">2</option><option value="3">3 · 普通</option><option value="4">4</option><option value="5">5 · 重要</option></select></label></div><label className="field"><span>隐私级别</span><select name="sensitivity" defaultValue={editingMemory === "new" ? "normal" : editingMemory.sensitivity}><option value="normal">普通</option><option value="sensitive">敏感（需单独授权）</option><option value="private">严格私密（永不发送）</option></select><small>严格私密内容不会进入任何模型请求。</small></label><div className="modal-actions"><button className="secondary-button" type="button" onClick={() => setEditingMemory(null)}>取消</button><button className="primary-button" type="submit">保存记忆</button></div></form></div>}
  </div>;
}
export default App;
