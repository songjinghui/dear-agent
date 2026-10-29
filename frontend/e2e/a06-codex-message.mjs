import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
const sessionTitle = process.env.ACCEPTANCE_SESSION_TITLE;
const messageIndex = Number(process.env.ACCEPTANCE_A06_INDEX);
if (!pageUrl || !evidenceDir || !Number.isInteger(messageIndex) || ![0, 1, 2].includes(messageIndex) || (messageIndex > 0 && !sessionTitle)) throw new Error("缺少 A-06 当前步骤所需参数。");

const messages = [
  "我最近在筹备表姐的婚礼，周末准备去试伴娘裙，颜色还在浅灰和雾蓝之间犹豫。",
  "试完以后我更喜欢雾蓝色那条，已经让店员帮我留到下周。鞋子还没决定穿哪双。",
  "今天和表姐确认了当天要提前到场帮她看流程，晚点要把时间空出来。",
];

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await page.locator("button.nav-item").filter({ hasText: "模型设置" }).click();
  await page.getByLabel("Agent 运行方式").selectOption("codex-app-server");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByText("已切换到 Codex Agent").waitFor({ state: "visible", timeout: 15_000 });
  await page.locator("button.nav-item").filter({ hasText: "对话" }).click();
  if (messageIndex === 0) {
    await page.locator("button.new-chat-button").click();
  } else {
    await page.locator("button.session-item").filter({ hasText: sessionTitle }).first().click();
    await page.waitForFunction((count) => document.querySelectorAll("article.message").length >= count, messageIndex * 2, { timeout: 30_000 });
  }

  const replies = page.locator(".message.assistant .message-text");
  const before = await replies.count();
  await page.getByPlaceholder("写下你此刻想说的话…").fill(messages[messageIndex]);
  await page.getByRole("button", { name: "发送" }).click();
  await page.waitForFunction((count) => document.querySelectorAll(".message.assistant .message-text").length > count, before, { timeout: 180_000 });
  const reply = replies.last();
  await page.waitForFunction(() => !document.querySelector(".message.assistant .streaming-cursor"), undefined, { timeout: 30_000 });
  const answer = (await reply.innerText()).trim();

  await mkdir(evidenceDir, { recursive: true });
  await page.screenshot({ path: join(evidenceDir, `ui-${messageIndex + 1}.png`), fullPage: true });
  await writeFile(join(evidenceDir, `result-${messageIndex + 1}.json`), JSON.stringify({
    provider: "codex-app-server", model: "gpt-6-astra", prompt: messages[messageIndex], answer,
  }, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({ answer }));
} finally {
  await browser.close();
}
