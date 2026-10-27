import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "./api";
import type { Message } from "./types";

type ChatStatus = "idle" | "processing" | "interrupted";
type ChatStatusResponse = { status: ChatStatus; error: string | null };

export function useChatSessionSync(
  active: boolean,
  sessionId: string,
  busy: boolean,
  setMessages: Dispatch<SetStateAction<Message[]>>,
) {
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [statusError, setStatusError] = useState("");
  const requestId = useRef(0);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const refresh = useCallback(async () => {
    if (!sessionId || busyRef.current) return;
    const currentRequest = ++requestId.current;
    const [messages, state] = await Promise.all([
      api<Message[]>(`/api/sessions/${encodeURIComponent(sessionId)}/messages`),
      api<ChatStatusResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/chat-status`),
    ]);
    if (currentRequest !== requestId.current) return;
    setMessages(messages);
    setStatus(state.status);
    setStatusError(state.error || "");
  }, [sessionId, setMessages]);

  useEffect(() => {
    if (!active || !sessionId) return;
    void refresh().catch(() => undefined);
  }, [active, sessionId, refresh]);

  useEffect(() => {
    if (!active || !sessionId) return;
    const onFocus = () => { void refresh().catch(() => undefined); };
    const onVisibility = () => {
      if (document.visibilityState === "visible") onFocus();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [active, sessionId, refresh]);

  useEffect(() => {
    // A live request already owns its optimistic assistant message. Polling here
    // would replace it with the server's incomplete history before the reply is saved.
    if (!active || !sessionId || busy || status !== "processing") return;
    let timer = 0;
    let cancelled = false;
    const poll = async () => {
      try { await refresh(); } catch { /* Keep polling through brief network interruptions. */ }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 2000);
    };
    timer = window.setTimeout(() => void poll(), 2000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [active, sessionId, busy, status, refresh]);

  return { status, statusError, refresh };
}
