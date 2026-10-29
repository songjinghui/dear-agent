import type { MemorySuggestion } from "./types";

export type MemorySuggestionGroup = {
  id: string;
  sessionId: string;
  title: string;
  items: MemorySuggestion[];
  content: string;
  memoryType: MemorySuggestion["memory_type"];
  importance: number;
  sensitivity: MemorySuggestion["sensitivity"];
};

const sensitivityRank = { normal: 0, sensitive: 1, private: 2 } as const;

function finalizeGroup(group: MemorySuggestionGroup) {
  group.content = `${group.title}\n${group.items.map((item) => `- ${item.content}`).join("\n")}`;
  group.importance = Math.max(...group.items.map((item) => item.importance));
  group.sensitivity = group.items.reduce(
    (current, item) => sensitivityRank[item.sensitivity] > sensitivityRank[current] ? item.sensitivity : current,
    group.items[0].sensitivity,
  );
  return group;
}

export function groupMemorySuggestions(items: MemorySuggestion[], sessionTitles: Record<string, string>): MemorySuggestionGroup[] {
  const groups = new Map<string, MemorySuggestionGroup>();
  for (const item of items) {
    const topic = item.topic?.trim();
    const groupId = topic ? `${item.session_id}:topic:${topic}` : `${item.session_id}:legacy`;
    const existing = groups.get(groupId);
    if (existing) {
      existing.items.push(item);
      continue;
    }
    groups.set(groupId, {
      id: groupId,
      sessionId: item.session_id,
      title: topic || sessionTitles[item.session_id] || "未命名会话",
      items: [item],
      content: "",
      memoryType: item.memory_type,
      importance: item.importance,
      sensitivity: item.sensitivity,
    });
  }
  return [...groups.values()].map(finalizeGroup);
}
