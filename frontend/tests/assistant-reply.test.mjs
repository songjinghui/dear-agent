import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const replySource = await readFile(
  new URL("../src/AssistantReply.tsx", import.meta.url),
  "utf8",
);
const appSource = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
const syncSource = await readFile(new URL("../src/useChatSessionSync.ts", import.meta.url), "utf8");

test("待回复状态会明确告知拾光正在回答", () => {
  assert.match(replySource, /role="status"/);
  assert.match(replySource, /拾光正在回答/);
  assert.match(replySource, /response-spinner/);
});

test("回复内容会逐字显现，并带有流式光标", () => {
  assert.match(replySource, /setInterval/);
  assert.match(replySource, /content\.slice\(0, displayedLength\)/);
  assert.match(replySource, /streaming-cursor/);
});

test("每次发送都会立即预留助手回复位置", () => {
  assert.match(appSource, /const optimisticAssistant: Message = \{/);
  assert.doesNotMatch(appSource, /const optimisticAssistant: Message \| null = streamCLI \?/);
  assert.match(appSource, /<AssistantReply/);
});

test("SSE 完成事件不会跳过正在动画的助手回复", () => {
  assert.match(appSource, /pendingAssistantReplacementRef/);
  assert.match(appSource, /setAnimatedMessageId\(optimisticAssistant\.id\)/);
  assert.match(appSource, /localId: optimisticAssistant\.id/);
});

test("正在通过 SSE 发送时，后台同步不会覆盖加载占位", () => {
  assert.match(syncSource, /if \(!active \|\| !sessionId \|\| busy \|\| status !== "processing"\) return;/);
});

test("窗口聚焦或可见性同步不会在活跃请求时替换本地助手占位", () => {
  assert.match(syncSource, /if \(!sessionId \|\| busyRef\.current\) return;/);
});
