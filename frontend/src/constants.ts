import type { Settings } from "./types";

export const emptySettings: Settings = {
  provider: "openai-compatible", api_protocol: "chat_completions", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini",
  temperature: 0.7, max_tokens: 1200, context_window_tokens: 8192, use_memories_in_cloud: false, use_vector_memory_search: false, memory_embedding_url: "http://127.0.0.1:11434/api/embed", memory_embedding_model: "", use_archive_history: false, default_mode: "chat", agent_definition: "", chat_mode_prompt: "", manager_mode_prompt: "", has_api_key: false,
  codex_available: false, has_codex_login: false, providers: [],
};

export const labels = { episodic: "经历", semantic: "偏好", working: "短期" };
export function formatBytes(bytes: number) { return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`; }
export const evidenceLabels: Record<string, string> = {
  explicit_fact: "明确陈述", subjective_experience: "主观感受", preference: "偏好", goal: "目标", inference: "推断",
};
