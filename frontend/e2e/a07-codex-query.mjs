import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
const prompt = process.env.ACCEPTANCE_PROMPT;
const sessionTitle = process.env.ACCEPTANCE_SESSION_TITLE;
const newChat = process.env.ACCEPTANCE_NEW_CHAT === "1";
const caseId = process.env.ACCEPTANCE_CASE_ID;
const sensitiveDecision = process.env.ACCEPTANCE_SENSITIVE_DECISION ?? "dismiss";
if (!pageUrl || !evidenceDir || !prompt || !caseId || (!newChat && !sessionTitle)) throw new Error("缺少 A-07 查询参数。");
if (!["dismiss", "accept"].includes(sensitiveDecision)) throw new Error("敏感记忆决策只能是 dismiss 或 accept。");

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  let dialogMessage = null;
  page.on("dialog", async (dialog) => {
    dialogMessage = dialog.message();
    if (sensitiveDecision === "accept") await dialog.accept();
    else await dialog.dismiss();
  });
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await page.locator("button.nav-item").filter({ hasText: "对话" }).click();
  if (newChat) await page.locator("button.new-chat-button").click();
  else await page.locator("button.session-item").filter({ hasText: sessionTitle }).first().click();
  await page.getByPlaceholder("写下你此刻想说的话…").waitFor({ state: "visible", timeout: 30_000 });
  const replies = page.locator(".message.assistant .message-text");
  const before = await replies.count();
  await page.getByPlaceholder("写下你此刻想说的话…").fill(prompt);
  await page.getByRole("button", { name: "发送" }).click();
  await page.waitForFunction((count) => document.querySelectorAll(".message.assistant .message-text").length > count, before, { timeout: 180_000 });
  const reply = replies.last();
  await page.waitForFunction(() => !document.querySelector(".message.assistant .streaming-cursor"), undefined, { timeout: 30_000 });
  const answer = (await reply.innerText()).trim();
  await mkdir(evidenceDir, { recursive: true });
  await page.screenshot({ path: join(evidenceDir, `${caseId}-query.png`), fullPage: true });
  await writeFile(join(evidenceDir, `${caseId}-query.json`), JSON.stringify({ prompt, answer, dialogMessage, sensitiveDecision }, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({ answer, dialogMessage }));
} finally {
  await browser.close();
}
