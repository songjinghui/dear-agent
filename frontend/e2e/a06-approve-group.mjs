import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const pageUrl = process.env.ACCEPTANCE_URL;
const evidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
if (!pageUrl || !evidenceDir) throw new Error("需要验收 URL 和证据目录。");

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await page.locator("button.nav-item").filter({ hasText: "记忆" }).click();
  const inbox = page.getByLabel("待确认的记忆建议");
  await inbox.waitFor({ state: "visible", timeout: 30_000 });
  const groupCount = await inbox.locator("strong").first().innerText();
  if (groupCount !== "1 个待确认主题") throw new Error(`候选主题数不符合预期：${groupCount}`);
  await page.getByRole("button", { name: /查看记忆建议/ }).click();
  await page.getByRole("button", { name: "保存这组" }).click();
  await page.getByText("已确认并保存到长期记忆").waitFor({ state: "visible", timeout: 30_000 });
  await page.getByText("筹备表姐婚礼", { exact: false }).waitFor({ state: "visible", timeout: 30_000 });
  await mkdir(evidenceDir, { recursive: true });
  await page.screenshot({ path: join(evidenceDir, "memory-approved.png"), fullPage: true });
  console.log(JSON.stringify({ groupCount }));
} finally {
  await browser.close();
}
