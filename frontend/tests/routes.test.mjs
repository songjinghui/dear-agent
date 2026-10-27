import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

async function loadRoutes() {
  const source = fileURLToPath(new URL("../src/routes.ts", import.meta.url));
  const compiler = fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url));
  const outputDir = await mkdtemp(join(tmpdir(), "diary-assistant-routes-"));
  try {
    execFileSync(process.execPath, [compiler, "--ignoreConfig", source, "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--outDir", outputDir, "--skipLibCheck"], { stdio: "pipe" });
    return await import(pathToFileURL(join(outputDir, "routes.js")).href);
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
}

test("页面路径可在刷新后恢复对应的页面", async () => {
  const { pageFromPathname, pathForPage } = await loadRoutes();

  assert.equal(pageFromPathname("/"), "chat");
  assert.equal(pageFromPathname("/memories"), "memories");
  assert.equal(pageFromPathname("/archives/"), "archives");
  assert.equal(pageFromPathname("/model-logs"), "model-logs");
  assert.equal(pageFromPathname("/unknown"), "chat");
  assert.equal(pathForPage("settings"), "/settings");
});

test("页面导航会写入浏览器历史，并响应后退和前进", async () => {
  const source = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");

  assert.match(source, /pageFromPathname\(window\.location\.pathname\)/);
  assert.match(source, /window\.history\.pushState/);
  assert.match(source, /window\.addEventListener\("popstate"/);
});
