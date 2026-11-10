import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("registered user can create and read a real durable metadata export", async ({ page }) => {
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated test account opt-in required");
  const email = process.env.E2E_ACCOUNT_EMAIL;
  const password = process.env.E2E_ACCOUNT_PASSWORD;
  if (!email || !password) throw new Error("Missing isolated test account credentials");
  await page.goto("/account");
  await page.getByLabel("电子邮箱").fill(email);
  await page.getByLabel("密码（12–256 字符）").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator('.account-module-nav a[href="#jobs"]').click();
  await expect(page.getByRole("heading", { name: "任务中心" })).toBeVisible();
  await page.getByRole("button", { name: "创建空间元数据导出" }).click();
  await expect(page.getByRole("button", { name: "创建空间元数据导出" })).toBeEnabled();
  await expect(async () => {
    await page.getByRole("button", { name: "刷新任务", exact: true }).click();
    await expect(page.getByRole("button", { name: "查看导出结果" }).first()).toBeVisible();
  }).toPass({ timeout: 40000, intervals: [1000, 2000] });
  await page.getByRole("button", { name: "查看导出结果" }).first().click();
  await expect(page.locator("pre")).toContainText('"schema_version": 1');
  await expect(page.locator("pre")).not.toContainText("storage_key");
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => /token|password/i.test(key)))).toEqual([]);
  await page.getByRole("button", { name: "退出并撤销会话" }).click();
  await expect(page.getByRole("heading", { name: "任务中心" })).toHaveCount(0);
});
