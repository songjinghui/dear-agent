import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
if (!pageUrl || !evidenceDir) throw new Error("需要验收 URL 和证据目录。");

const memories = [
  { content: "验收普通记忆：我更喜欢低因咖啡。", sensitivity: "normal" },
  { content: "验收敏感记忆：我对橙子过敏。", sensitivity: "sensitive" },
  { content: "验收严格私密：我的测试代号是蓝鹊。", sensitivity: "private" },
];

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await page.locator("button.nav-item").filter({ hasText: "模型设置" }).click();
  await page.getByLabel("Agent 运行方式").selectOption("codex-app-server");
  const memorySwitch = page.locator('button[role="switch"]').first();
  if (await memorySwitch.getAttribute("aria-checked") !== "true") await memorySwitch.click();
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.getByText("已切换到 Codex Agent").waitFor({ state: "visible", timeout: 15_000 });

  await page.locator("button.nav-item").filter({ hasText: "记忆" }).click();
  for (const memory of memories) {
    await page.getByRole("button", { name: /新增记忆/ }).click();
    await page.locator('textarea[name="content"]').fill(memory.content);
    await page.locator('select[name="sensitivity"]').selectOption(memory.sensitivity);
    await page.getByRole("button", { name: "保存记忆" }).click();
    await page.getByText("记忆已保存").waitFor({ state: "visible", timeout: 15_000 });
  }
  await mkdir(evidenceDir, { recursive: true });
  await page.screenshot({ path: join(evidenceDir, "memory-setup.png"), fullPage: true });
} finally {
  await browser.close();
}
