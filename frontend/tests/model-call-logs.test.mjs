import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const componentPath = new URL("../src/ModelCallLogs.tsx", import.meta.url);

test("model call logs disclose their thirty-day local retention period", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /保留 30 天/);
  assert.match(source, /到期后自动删除/);
});
