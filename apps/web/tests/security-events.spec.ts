import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("account security history shows persisted own events and clears after logout", async ({ page }) => {
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated account opt-in required");
  const email = process.env.E2E_ACCOUNT_EMAIL;
  const password = process.env.E2E_ACCOUNT_PASSWORD;
  if (!email || !password) throw new Error("Missing isolated credentials");
  await page.goto("/account");
  expect((await page.request.get("/api/account/auth/security-events")).status()).toBe(401);
  expect((await page.request.post("/api/account/auth/security-events")).status()).toBe(405);
  await page.getByLabel("电子邮箱").fill(email);
  await page.getByLabel("密码（12–256 字符）").fill("incorrect-password-2026");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("status", { name: "账户状态" })).toHaveText("INVALID_CREDENTIALS");
  await page.getByLabel("密码（12–256 字符）").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator('.account-module-nav a[href="#security"]').click();
  const history = page.getByRole("region", { name: "账户安全历史" });
  await history.getByRole("button", { name: "刷新账户安全历史" }).click();
  await expect(history.getByRole("status")).toHaveText("已加载真实账户安全历史。");
  await expect(history.getByText("账户注册", { exact: true })).toBeVisible();
  await expect(history.getByText("登录成功", { exact: true }).first()).toBeVisible();
  await expect(history.getByText("针对账户的登录失败尝试", { exact: true })).toBeVisible();
  expect(await history.textContent()).not.toContain(email);
  expect(await history.textContent()).not.toContain(password);
  await page.getByRole("button", { name: "退出并撤销会话" }).click();
  await expect(history).toHaveCount(0);
  await page.getByLabel("密码（12–256 字符）").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator('.account-module-nav a[href="#security"]').click();
  await expect(history.getByRole("status")).toHaveText("尚未加载账户安全历史。");
  await expect(history.getByRole("listitem")).toHaveCount(0);
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => /token|password/i.test(key)))).toEqual([]);
});
