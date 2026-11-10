import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("account form uses a real API and keeps anonymous requests unauthorized", async ({ page }) => {
  await page.goto("/account");
  await expect(page.getByRole("heading", { name: "账户与工作空间" })).toBeVisible();
  const anonymous = await page.request.get("/api/account/auth/me");
  expect(anonymous.status()).toBe(401);
  await page.getByLabel("电子邮箱").fill(`not-registered-${Date.now()}@example.invalid`);
  await page.getByLabel("密码（12–256 字符）").fill("incorrect-password-2026");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("status", { name: "账户状态" })).toHaveText("INVALID_CREDENTIALS");
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => /token|password/i.test(key)))).toEqual([]);
});
