import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

async function loadGroups() {
  const source = fileURLToPath(new URL("../src/memorySuggestionGroups.ts", import.meta.url));
  const compiler = fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url));
  const outputDir = await mkdtemp(join(tmpdir(), "diary-assistant-memory-groups-"));
  try {
    execFileSync(process.execPath, [compiler, "--ignoreConfig", source, "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--outDir", outputDir, "--skipLibCheck"], { stdio: "pipe" });
    return await import(pathToFileURL(join(outputDir, "memorySuggestionGroups.js")).href);
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
}

test("同一会话的待整理记忆归为一组，但不合并独立事实", async () => {
  const { groupMemorySuggestions } = await loadGroups();
  const groups = groupMemorySuggestions([
    { id: "a", session_id: "xinjiang", content: "时间", memory_type: "working", importance: 3, sensitivity: "normal", evidence: "时间", rationale: "" },
    { id: "b", session_id: "xinjiang", content: "装备", memory_type: "working", importance: 3, sensitivity: "normal", evidence: "装备", rationale: "" },
    { id: "c", session_id: "health", content: "睡眠", memory_type: "working", importance: 3, sensitivity: "normal", evidence: "睡眠", rationale: "" },
  ], { xinjiang: "北疆看雪之旅" });

  assert.deepEqual(groups.map((group) => [group.title, group.items.map((item) => item.id)]), [
    ["北疆看雪之旅", ["a", "b"]],
    ["未命名会话", ["c"]],
  ]);
  assert.equal(groups[0].content, "北疆看雪之旅\n- 时间\n- 装备");
});

test("同一会话里的不同主题不能被合并", async () => {
  const { groupMemorySuggestions } = await loadGroups();
  const groups = groupMemorySuggestions([
    { id: "trip", session_id: "daily", topic: "北疆旅行", content: "准备雪鞋", memory_type: "working", importance: 3, sensitivity: "normal", evidence: "准备雪鞋", rationale: "" },
    { id: "sleep", session_id: "daily", topic: "睡眠调整", content: "本周想早睡", memory_type: "working", importance: 3, sensitivity: "normal", evidence: "本周想早睡", rationale: "" },
  ], { daily: "日常记录" });

  assert.deepEqual(groups.map((group) => [group.title, group.items.map((item) => item.id)]), [
    ["北疆旅行", ["trip"]],
    ["睡眠调整", ["sleep"]],
  ]);
});

test("侧栏按待确认主题计数，而不是按候选详情计数", async () => {
  const { groupMemorySuggestions } = await loadGroups();
  const groups = groupMemorySuggestions([
    { id: "trip-date", session_id: "daily", topic: "北疆旅行", content: "十一月出发", memory_type: "working", importance: 3, sensitivity: "normal", evidence: "十一月出发", rationale: "" },
    { id: "trip-gear", session_id: "daily", topic: "北疆旅行", content: "准备雪鞋", memory_type: "working", importance: 3, sensitivity: "normal", evidence: "准备雪鞋", rationale: "" },
    { id: "sleep", session_id: "daily", topic: "睡眠调整", content: "本周想早睡", memory_type: "working", importance: 3, sensitivity: "normal", evidence: "本周想早睡", rationale: "" },
  ], {});
  const appSource = await readFile(fileURLToPath(new URL("../src/App.tsx", import.meta.url)), "utf8");

  assert.equal(groups.length, 2);
  assert.match(appSource, /groupMemorySuggestions\(memorySuggestions, \{\}\)\.length/);
  assert.match(appSource, /\$\{pendingMemoryTopicCount\} 个待确认主题/);
});
