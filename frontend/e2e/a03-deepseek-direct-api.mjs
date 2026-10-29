import { chromium } from "playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
const action = process.env.A03_ACTION;
const turnIndex = Number(process.env.A03_TURN_INDEX);
const turnStart = Number(process.env.A03_TURN_START);
const turnEnd = Number(process.env.A03_TURN_END);
const maxTokens = process.env.A03_MAX_TOKENS || "350";
const sessionAnchor = process.env.A03_SESSION_ANCHOR || "下个月是小澄的生日";
if (!pageUrl || !evidenceDir || !["preflight", "turn", "turns", "probe"].includes(action || "")) {
  throw new Error("缺少 A-03 验收所需的 URL、证据目录或执行动作。");
}

const fixturePath = resolve(process.cwd(), "..", "docs", "fixtures", "v1-acceptance", "a03-live-dialogue.md");
const fixture = await readFile(fixturePath, "utf8");
const execution = fixture.match(/## 执行输入\s*([\s\S]*?)\s*## 最后追问\s*([\s\S]*?)\s*## 若尚未触发压缩/);
if (!execution) throw new Error("无法从 A-03 fixture 读取执行输入。");
const turns = [...execution[1].matchAll(/(?:^|\n)\d+\.\s+([\s\S]*?)(?=\n\n\d+\.\s+|\s*$)/g)].map((item) => item[1].trim());
const probe = execution[2].trim();
if (turns.length !== 14) throw new Error(`A-03 fixture 应有 14 条输入，实际为 ${turns.length} 条。`);

async function setSwitchOff(page, label) {
  const toggle = page.locator(".privacy-setting").filter({ hasText: label }).getByRole("switch");
  if (await toggle.getAttribute("aria-checked") === "true") await toggle.click();
}

async function configureDirectApi(page) {
  await page.locator("button.nav-item").filter({ hasText: "模型设置" }).click();
  await page.getByLabel("Agent 运行方式").selectOption("openai-compatible");
  await page.getByLabel("API 地址").fill("https://api.deepseek.com");
  await page.getByLabel("模型名称").fill("deepseek-flash");
  await page.getByLabel("API 协议").selectOption("chat_completions");
  await page.getByLabel("温度").fill("0.2");
  await page.getByLabel("最大输出长度").fill(maxTokens);
  await page.getByLabel("模型上下文窗口").fill("4096");
  await setSwitchOff(page, "允许把相关普通长期记忆加入模型请求");
  await setSwitchOff(page, "启用本机向量语义检索");
  await setSwitchOff(page, "允许检索历史档案并附加到模型请求");
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByText("设置已保存", { exact: false }).waitFor({ state: "visible", timeout: 15_000 });
  await page.locator("button.nav-item").filter({ hasText: "对话" }).click();
}

async function openTargetSession(page, create) {
  if (create) {
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
    return;
  }
  const session = page.locator("button.session-item").filter({ hasText: sessionAnchor }).first();
  await session.waitFor({ state: "visible", timeout: 15_000 });
  await session.click();
  await page.getByPlaceholder("写下你此刻想说的话…").waitFor({ state: "visible", timeout: 15_000 });
}

async function sendAndCapture(page, prompt, label) {
  const replies = page.locator(".message.assistant .message-text");
  const before = await replies.count();
  await page.getByPlaceholder("写下你此刻想说的话…").fill(prompt);
  await page.getByRole("button", { name: "发送" }).click();
  await page.waitForFunction((count) => document.querySelectorAll(".message.assistant .message-text").length > count, before, { timeout: 120_000 });
  await page.waitForFunction(() => !document.querySelector(".message.assistant .streaming-cursor"), undefined, { timeout: 45_000 });
  const answer = (await replies.last().innerText()).trim();
  if (!answer) throw new Error("模型返回了空回答。");
  await mkdir(evidenceDir, { recursive: true });
  await page.screenshot({ path: join(evidenceDir, `ui-${label}.png`), fullPage: true });
  await writeFile(join(evidenceDir, `result-${label}.json`), JSON.stringify({ prompt, answer }, null, 2) + "\n", "utf8");
  return answer;
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  let prompt;
  let label;
  if (action === "turns") {
    if (!Number.isInteger(turnStart) || !Number.isInteger(turnEnd) || turnStart < 1 || turnEnd > turns.length || turnStart > turnEnd) {
      throw new Error(`A-03 turn 区间必须在 1 到 ${turns.length} 之间。`);
    }
    await openTargetSession(page, turnStart === 1);
    const answers = [];
    for (let index = turnStart; index <= turnEnd; index += 1) {
      const itemLabel = `turn-${String(index).padStart(2, "0")}`;
      answers.push({ index, answer: await sendAndCapture(page, turns[index - 1], itemLabel) });
    }
    console.log(JSON.stringify({ action, start: turnStart, end: turnEnd, sessionAnchor, answers }));
  } else {
    if (action === "preflight") {
    await configureDirectApi(page);
    await openTargetSession(page, true);
    prompt = "请简短回复：配置验证成功。";
    label = "preflight";
    } else if (action === "turn") {
    if (!Number.isInteger(turnIndex) || turnIndex < 1 || turnIndex > turns.length) {
      throw new Error(`A-03 turn 序号必须在 1 到 ${turns.length} 之间。`);
    }
    await openTargetSession(page, turnIndex === 1);
    prompt = turns[turnIndex - 1];
    label = `turn-${String(turnIndex).padStart(2, "0")}`;
    } else {
    await openTargetSession(page, false);
    prompt = probe;
    label = "probe";
    }
    const answer = await sendAndCapture(page, prompt, label);
    console.log(JSON.stringify({ action, label, sessionAnchor: action === "preflight" ? null : sessionAnchor, answer }));
  }
} finally {
  await browser.close();
}
