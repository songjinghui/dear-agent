import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
if (!pageUrl || !evidenceDir) throw new Error("需要 ACCEPTANCE_URL 和 ACCEPTANCE_EVIDENCE_DIR。");

const prompt = "想简单确认一下：你能陪我聊聊吗？";
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /模型设置/ }).click();
  await page.getByLabel("Agent 运行方式").selectOption("codex-app-server");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByText("已切换到 Codex Agent").waitFor({ state: "visible", timeout: 15_000 });
  await page.locator("button.nav-item").filter({ hasText: "对话" }).click();
  await page.getByPlaceholder("写下你此刻想说的话…").fill(prompt);
  await page.getByRole("button", { name: "发送" }).click();

  const reply = page.locator(".message.assistant .message-text").last();
  await reply.waitFor({ state: "visible", timeout: 180_000 });
  await page.waitForFunction(() => !document.querySelector(".message.assistant .streaming-cursor"), undefined, { timeout: 30_000 });
  const answer = (await reply.innerText()).trim();
  if (!answer) throw new Error("Codex 没有返回文字。 ");

  await mkdir(evidenceDir, { recursive: true });
  await page.screenshot({ path: join(evidenceDir, "ui.png"), fullPage: true });
  await writeFile(join(evidenceDir, "result.json"), JSON.stringify({
    provider: "codex-app-server", model: "gpt-6-astra", prompt, answer,
  }, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({ provider: "codex-app-server", model: "gpt-6-astra", answer }));
} finally {
  await browser.close();
}
