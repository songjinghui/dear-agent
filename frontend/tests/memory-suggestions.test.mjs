import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const componentPath = new URL("../src/MemorySuggestions.tsx", import.meta.url);

test("chat memory suggestions are collapsed by default and opened on request", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /useState\(false\)/);
  assert.match(source, /aria-expanded=\{expanded\}/);
  assert.match(source, /查看记忆建议/);
  assert.match(source, /\{expanded\s*&&/);
  assert.match(source, /items\.map\(\(item\) =>/);
  assert.match(source, /if \(hasNewSuggestions\) setExpanded\(false\)/);
});

test("suggestion review shows only save and dismiss before optional details", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /保存/);
  assert.match(source, /忽略/);
  assert.match(source, /<details/);
  assert.match(source, /依据/);
});

test("已有待整理项会按事项主题归组，历史候选才回退到来源会话", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /sessionTitles/);
  assert.match(source, /groupMemorySuggestions/);
  assert.match(source, /group\.id/);
  assert.match(source, /memory-suggestion-group/);
  assert.match(source, /保存为一条主题记忆/);
});

test("同一主题以一个整体供用户保存，而不是逐条写入长期记忆", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /onApproveGroup/);
  assert.match(source, /保存这组/);
  assert.match(source, /group\.content/);
});
