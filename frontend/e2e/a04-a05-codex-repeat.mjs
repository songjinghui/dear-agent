import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
const archivePath = process.env.ACCEPTANCE_ARCHIVE_PATH;
const sessionTitle = process.env.ACCEPTANCE_SESSION_TITLE;
const importArchive = process.env.ACCEPTANCE_IMPORT_ARCHIVE === "1";
const promptIndex = Number(process.env.ACCEPTANCE_PROMPT_INDEX);
if (!pageUrl || !evidenceDir || !sessionTitle || !Number.isInteger(promptIndex) || ![0, 1].includes(promptIndex) || (importArchive && !archivePath)) throw new Error("缺少当前重复所需的验收参数。");

const prompts = ["我这趟出行现在是怎么安排的？", "我住的那间民宿叫什么？"];
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await page.locator("button.nav-item").filter({ hasText: "模型设置" }).click();
  await page.getByLabel("Agent 运行方式").selectOption("codex-app-server");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByText("已切换到 Codex Agent").waitFor({ state: "visible", timeout: 15_000 });
  if (promptIndex === 0) {
    await page.locator("button.nav-item").filter({ hasText: "历史档案" }).click();
    if (importArchive) {
      await page.locator("input.hidden-file-input").setInputFiles(archivePath);
      await page.getByText("导入前选择会话").waitFor({ state: "visible", timeout: 30_000 });
      await page.getByRole("button", { name: "导入已选会话" }).click();
    }

    const archiveSession = page.locator("button.archive-session-row").filter({ hasText: sessionTitle });
    await archiveSession.waitFor({ state: "visible", timeout: 30_000 });
    await archiveSession.click();
    await page.getByRole("button", { name: "继续此会话" }).click();
    await page.getByPlaceholder("写下你此刻想说的话…").waitFor({ state: "visible", timeout: 30_000 });
    await page.waitForFunction(() => document.querySelectorAll("article.message").length >= 8, undefined, { timeout: 30_000 });
  } else {
    await page.locator("button.session-item").filter({ hasText: sessionTitle }).first().click();
    await page.getByPlaceholder("写下你此刻想说的话…").waitFor({ state: "visible", timeout: 30_000 });
    await page.waitForFunction(() => document.querySelectorAll("article.message").length >= 10, undefined, { timeout: 30_000 });
  }

  const prompt = prompts[promptIndex];
  const replies = page.locator(".message.assistant .message-text");
  const before = await replies.count();
  await page.getByPlaceholder("写下你此刻想说的话…").fill(prompt);
  await page.getByRole("button", { name: "发送" }).click();
  await page.waitForFunction((count) => document.querySelectorAll(".message.assistant .message-text").length > count, before, { timeout: 180_000 });
  const reply = replies.last();
  await page.waitForFunction(() => !document.querySelector(".message.assistant .streaming-cursor"), undefined, { timeout: 30_000 });
  const answer = (await reply.innerText()).trim();

  await mkdir(evidenceDir, { recursive: true });
  await page.screenshot({ path: join(evidenceDir, `ui-${promptIndex + 1}.png`), fullPage: true });
  await writeFile(join(evidenceDir, `result-${promptIndex + 1}.json`), JSON.stringify({
    provider: "codex-app-server", model: "gpt-6-astra", prompt, answer,
  }, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({ provider: "codex-app-server", model: "gpt-6-astra", sessionTitle, prompt, answer }));
} finally {
  await browser.close();
}
