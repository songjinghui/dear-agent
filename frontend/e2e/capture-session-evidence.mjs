import { chromium } from "playwright";

const pageUrl = process.env.ACCEPTANCE_URL;
const sessionTitle = process.env.ACCEPTANCE_SESSION_TITLE;
const expectedReply = process.env.ACCEPTANCE_EXPECTED_REPLY;
const screenshotPath = process.env.ACCEPTANCE_SCREENSHOT_PATH;
if (!pageUrl || !sessionTitle || !expectedReply || !screenshotPath) throw new Error("缺少会话证据截图所需参数。");

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await page.locator("button.session-item").filter({ hasText: sessionTitle }).first().click();
  await page.getByText(expectedReply, { exact: false }).waitFor({ state: "visible", timeout: 30_000 });
  await page.screenshot({ path: screenshotPath, fullPage: true });
} finally {
  await browser.close();
}
