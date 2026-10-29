import { expect, test } from "@playwright/test";

test("聊天首页可用，侧栏切换会写入路由且刷新后保留", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "今天，想从哪里聊起？" })).toBeVisible();
  await expect(page.getByPlaceholder("写下你此刻想说的话…")).toBeVisible();

  await page.getByRole("button", { name: /记忆/ }).click();
  await expect(page).toHaveURL(/\/memories$/);
  await expect(page.getByRole("heading", { name: "我的记忆" })).toBeVisible();

  await page.reload();
  await expect(page).toHaveURL(/\/memories$/);
  await expect(page.getByRole("heading", { name: "我的记忆" })).toBeVisible();
});
