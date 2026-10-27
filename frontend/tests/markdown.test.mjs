import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";

async function loadMarkdownModule() {
  const source = fileURLToPath(new URL("../src/markdown.ts", import.meta.url));
  const compiler = fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url));
  const outputDir = await mkdtemp(join(tmpdir(), "diary-assistant-markdown-"));
  try {
    execFileSync(process.execPath, [compiler, "--ignoreConfig", source, "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--outDir", outputDir, "--skipLibCheck"], { stdio: "pipe" });
    return await import(pathToFileURL(join(outputDir, "markdown.js")).href);
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
}

test("助手回复的 Markdown 会区分标题、列表、引用和代码块", async () => {
  const { parseMarkdownBlocks } = await loadMarkdownModule();
  const blocks = parseMarkdownBlocks("# 行程\n\n- 机票\n- 酒店\n\n> 出发前看预报\n\n```text\n-20°C\n```");

  assert.deepEqual(blocks.map((block) => block.type), ["heading", "unordered-list", "quote", "code"]);
  assert.equal(blocks[0].level, 1);
  assert.deepEqual(blocks[1].items, ["机票", "酒店"]);
  assert.equal(blocks[3].content, "-20°C");
});

test("渲染的链接仅允许安全协议", async () => {
  const { safeMarkdownHref } = await loadMarkdownModule();

  assert.equal(safeMarkdownHref("https://example.com"), "https://example.com");
  assert.equal(safeMarkdownHref("mailto:hello@example.com"), "mailto:hello@example.com");
  assert.equal(safeMarkdownHref("javascript:alert(1)"), null);
  assert.equal(safeMarkdownHref("data:text/html,hello"), null);
});

test("渲染器使用语义结构且不执行模型返回的 HTML", async () => {
  const source = await readFile(new URL("../src/MarkdownMessage.tsx", import.meta.url), "utf8");

  assert.match(source, /parseMarkdownBlocks/);
  assert.match(source, /<blockquote/);
  assert.match(source, /<pre/);
  assert.match(source, /<table/);
  assert.doesNotMatch(source, /dangerouslySetInnerHTML/);
});
