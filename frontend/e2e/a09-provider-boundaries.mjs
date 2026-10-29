import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
if (!pageUrl || !evidenceDir) throw new Error("需要 ACCEPTANCE_URL 和 ACCEPTANCE_EVIDENCE_DIR。");

async function setSwitchOff(page, label) {
  const toggle = page.locator(".privacy-setting").filter({ hasText: label }).getByRole("switch");
  if (await toggle.getAttribute("aria-checked") === "true") await toggle.click();
}

async function saveDirectApiSettings(page) {
  await page.locator("button.nav-item").filter({ hasText: "模型设置" }).click();
  await page.getByLabel("Agent 运行方式").selectOption("openai-compatible");
  await page.getByLabel("API 地址").fill("https://api.deepseek.com");
  await page.getByLabel("模型名称").fill("deepseek-flash");
  await page.getByLabel("API 协议").selectOption("chat_completions");
  await page.getByLabel("温度").fill("0.2");
  await page.getByLabel("最大输出长度").fill("350");
  await page.getByLabel("模型上下文窗口").fill("4096");
  await setSwitchOff(page, "允许把相关普通长期记忆加入模型请求");
  await setSwitchOff(page, "启用本机向量语义检索");
  await setSwitchOff(page, "允许检索历史档案并附加到模型请求");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByText("设置已保存", { exact: false }).waitFor({ state: "visible", timeout: 15_000 });
}

async function saveCodexSettings(page) {
  await page.locator("button.nav-item").filter({ hasText: "模型设置" }).click();
  await page.getByLabel("Agent 运行方式").selectOption("codex-app-server");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByText("已切换到 Codex Agent").waitFor({ state: "visible", timeout: 15_000 });
}

async function startFreshChat(page) {
  await page.locator("button.nav-item").filter({ hasText: "对话" }).click();
  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/api/sessions") && response.request().method() === "POST" && response.ok()),
    page.locator("button.new-chat-button").click(),
  ]);
  await page.waitForFunction(
    () => document.querySelector("button.session-item.selected")?.textContent?.includes("新的对话")
      && document.querySelectorAll("article.message").length === 0,
    undefined,
    { timeout: 15_000 },
  );
}

async function sendAndCapture(page, provider, prompt) {
  const replies = page.locator(".message.assistant .message-text");
  const before = await replies.count();
  await page.getByPlaceholder("写下你此刻想说的话…").fill(prompt);
  await page.getByRole("button", { name: "发送" }).click();
  await page.waitForFunction((count) => document.querySelectorAll(".message.assistant .message-text").length > count, before, { timeout: 180_000 });
  await page.waitForFunction(() => !document.querySelector(".message.assistant .streaming-cursor"), undefined, { timeout: 45_000 });
  const answer = (await replies.last().innerText()).trim();
  if (!answer) throw new Error(`${provider} 返回了空回答。`);
  await page.screenshot({ path: join(evidenceDir, `ui-${provider}.png`), fullPage: true });
  return { prompt, answer };
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await mkdir(evidenceDir, { recursive: true });

  await saveDirectApiSettings(page);
  await startFreshChat(page);
  const direct = await sendAndCapture(page, "openai-compatible", "请用一句话确认你收到了这条消息。");

  await saveCodexSettings(page);
  await startFreshChat(page);
  const codex = await sendAndCapture(page, "codex-app-server", "请用一句话确认这是一段新的对话。");

  const result = {
    direct_api: {
      provider: "openai-compatible",
      model: "deepseek-flash",
      protocol: "chat_completions",
      base_url: "https://api.deepseek.com",
      temperature: 0.2,
      max_tokens: 350,
      context_window_tokens: 4096,
      ...direct,
    },
    codex_cli: {
      provider: "codex-app-server",
      model: "gpt-6-astra",
      ...codex,
    },
  };
  await writeFile(join(evidenceDir, "result.json"), JSON.stringify(result, null, 2) + "\n", "utf8");
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
