import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appPath = new URL("../src/App.tsx", import.meta.url);

test("待整理记忆不打断聊天，但会固定出现在记忆页首屏", async () => {
  const appSource = await readFile(appPath, "utf8");
  const chatMarkup = appSource.slice(
    appSource.indexOf('{page === "chat" && <section'),
    appSource.indexOf('{page === "archives" && <section'),
  );
  const memoriesMarkup = appSource.slice(
    appSource.indexOf('{page === "memories" && memorySuggestions'),
    appSource.indexOf('{page === "model-logs"'),
  );

  assert.doesNotMatch(chatMarkup, /<MemorySuggestions/);
  assert.match(memoriesMarkup, /memorySuggestions\.length > 0/);
  assert.match(memoriesMarkup, /<MemorySuggestions/);
  assert.ok(memoriesMarkup.indexOf("<MemorySuggestions") < memoriesMarkup.indexOf('className="memory-toolbar"'));
  assert.match(appSource, /nav-pending-count/);
  assert.match(appSource, /api<MemorySuggestion\[\]>\("\/api\/memory-suggestions"\)/);
  assert.match(appSource, /sessionTitles=\{Object\.fromEntries\(sessions\.map/);
});
