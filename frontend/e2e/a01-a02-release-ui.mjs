import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
if (!pageUrl || !evidenceDir) throw new Error("需要 ACCEPTANCE_URL 和 ACCEPTANCE_EVIDENCE_DIR。");

const markdownPrompt = "请只用 Markdown 回复，不要解释：\n## 回复状态验收\n- 能看到加载状态\n- 离开后可以恢复\n\n`Shiguang`";
const errorPrompt = "这条消息用于验证错误状态。";

async function configureCodex(page) {
  await page.locator("button.nav-item").filter({ hasText: "模型设置" }).click();
  await page.getByLabel("Agent 运行方式").selectOption("codex-app-server");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByText("已切换到 Codex Agent").waitFor({ state: "visible", timeout: 15_000 });
  await page.locator("button.nav-item").filter({ hasText: "对话" }).click();
}

async function send(page, prompt) {
  await page.getByPlaceholder("写下你此刻想说的话…").fill(prompt);
  await page.getByRole("button", { name: "发送" }).click();
}

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext();
  const firstPage = await context.newPage();
  await firstPage.goto(pageUrl, { waitUntil: "networkidle" });
  await configureCodex(firstPage);
  await Promise.all([
    firstPage.waitForResponse((response) => response.url().endsWith("/api/sessions") && response.request().method() === "POST" && response.ok()),
    firstPage.getByRole("button", { name: /新建对话/ }).click(),
  ]);
  await firstPage.waitForFunction(() => document.querySelector("button.session-item.selected")?.textContent?.includes("新的对话"));
  const chatResponse = firstPage.waitForResponse((response) =>
    response.url().endsWith("/api/chat")
    && response.request().method() === "POST"
    && response.ok()
    && response.headers()["content-type"]?.includes("text/event-stream"),
  { timeout: 30_000 });
  await send(firstPage, markdownPrompt);

  const pending = firstPage.locator(".assistant-pending");
  await pending.waitFor({ state: "visible", timeout: 30_000 });
  const loadingEvidence = {
    assistantAvatar: await firstPage.locator(".message.assistant .avatar").last().innerText(),
    pendingText: await pending.innerText(),
    spinnerVisible: await firstPage.locator(".assistant-pending .response-spinner").isVisible(),
  };
  if (loadingEvidence.assistantAvatar !== "拾" || !loadingEvidence.pendingText.includes("拾光正在回答") || !loadingEvidence.spinnerVisible) {
    throw new Error(`加载状态不完整：${JSON.stringify(loadingEvidence)}`);
  }
  await chatResponse;
  await mkdir(evidenceDir, { recursive: true });
  await firstPage.screenshot({ path: join(evidenceDir, "a02-loading.png"), fullPage: true });

  // Closing the page intentionally disconnects the SSE consumer. The server must
  // finish the already-started local CLI turn and persist it for a later page.
  await firstPage.close();

  const recoveredPage = await context.newPage();
  await recoveredPage.goto(pageUrl, { waitUntil: "networkidle" });
  await recoveredPage.locator("button.session-item").filter({ hasText: markdownPrompt.slice(0, 28) }).first().click();
  const reply = recoveredPage.locator(".message.assistant .message-text").last();
  await reply.waitFor({ state: "visible", timeout: 180_000 });
  await recoveredPage.waitForFunction(() => !document.querySelector(".message.assistant .streaming-cursor"), undefined, { timeout: 45_000 });

  const markdownEvidence = {
    heading: await recoveredPage.locator(".message.assistant .message-markdown h2").last().innerText(),
    items: await reply.locator(".message-markdown ul li").allInnerTexts(),
    code: await recoveredPage.locator(".message.assistant .message-markdown code").last().innerText(),
    answer: (await reply.innerText()).trim(),
  };
  if (markdownEvidence.heading !== "回复状态验收" || markdownEvidence.items.length !== 2 || markdownEvidence.code !== "Shiguang") {
    throw new Error(`Markdown 没有按预期呈现：${JSON.stringify(markdownEvidence)}`);
  }
  await recoveredPage.screenshot({ path: join(evidenceDir, "a01-recovered-a02-markdown.png"), fullPage: true });

  await recoveredPage.locator("button.nav-item").filter({ hasText: "模型设置" }).click();
  await recoveredPage.getByLabel("Agent 运行方式").selectOption("openai-compatible");
  await recoveredPage.getByLabel("API 地址").fill("http://127.0.0.1:9/v1");
  await recoveredPage.getByLabel("模型名称").fill("acceptance-unavailable-model");
  await recoveredPage.getByRole("button", { name: "保存设置" }).click();
  await recoveredPage.getByText("设置已保存").waitFor({ state: "visible", timeout: 15_000 });
  await recoveredPage.locator("button.nav-item").filter({ hasText: "对话" }).click();
  await recoveredPage.getByRole("button", { name: /新建对话/ }).click();
  await send(recoveredPage, errorPrompt);
  const error = recoveredPage.locator(".error-toast");
  await error.waitFor({ state: "visible", timeout: 30_000 });
  const errorText = (await error.innerText()).trim();
  if (!errorText || /api[_ -]?key/i.test(errorText)) throw new Error(`错误态不可理解或泄露凭据：${errorText}`);
  await recoveredPage.screenshot({ path: join(evidenceDir, "a02-error.png"), fullPage: true });

  await writeFile(join(evidenceDir, "result.json"), JSON.stringify({
    provider: "codex-app-server",
    loading: loadingEvidence,
    recovery: { answer: markdownEvidence.answer },
    markdown: markdownEvidence,
    error: { prompt: errorPrompt, text: errorText },
  }, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({ loading: loadingEvidence, markdown: markdownEvidence, error: errorText }));
} finally {
  await browser.close();
}
