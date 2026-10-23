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
});

test("suggestion review shows only save and dismiss before optional details", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /保存/);
  assert.match(source, /忽略/);
  assert.match(source, /<details/);
  assert.match(source, /依据/);
});
