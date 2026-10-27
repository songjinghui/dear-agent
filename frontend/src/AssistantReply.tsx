import { useEffect, useRef, useState } from "react";
import { MarkdownMessage } from "./MarkdownMessage";

type AssistantReplyProps = {
  content: string;
  waiting: boolean;
  streaming: boolean;
  revealing: boolean;
  onRevealComplete: () => void;
};

export function AssistantReply({
  content,
  waiting,
  streaming,
  revealing,
  onRevealComplete,
}: AssistantReplyProps) {
  const animate = streaming || revealing;
  const [displayedLength, setDisplayedLength] = useState(() => (animate ? 0 : content.length));
  const completedReveal = useRef(false);

  useEffect(() => {
    if (!animate) {
      setDisplayedLength(content.length);
      return undefined;
    }
    if (!content) return undefined;

    const timer = window.setInterval(() => {
      setDisplayedLength((current) => {
        const remaining = content.length - current;
        if (remaining <= 0) return current;
        return Math.min(content.length, current + Math.max(1, Math.ceil(remaining / 8)));
      });
    }, 16);
    return () => window.clearInterval(timer);
  }, [animate, content]);

  useEffect(() => {
    if (!revealing) {
      completedReveal.current = false;
      return;
    }
    if (content && displayedLength >= content.length && !completedReveal.current) {
      completedReveal.current = true;
      onRevealComplete();
    }
  }, [content, displayedLength, onRevealComplete, revealing]);

  if (waiting) {
    return <div className="assistant-pending" role="status" aria-live="polite">
      <span className="response-spinner" aria-hidden="true" />
      <span>拾光正在回答</span>
      <span className="loading-ellipsis" aria-hidden="true"><i /><i /><i /></span>
    </div>;
  }

  return <div className={`message-text${animate ? " is-flowing" : ""}`}>
    <MarkdownMessage content={content.slice(0, displayedLength)} />
    {animate && <span className="streaming-cursor" aria-hidden="true">▍</span>}
  </div>;
}
