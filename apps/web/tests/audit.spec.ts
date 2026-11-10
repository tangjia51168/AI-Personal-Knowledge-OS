import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("administrator reads real persisted audit events through the same-origin proxy", async ({ page }) => {
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated account opt-in required");
  const email = process.env.E2E_ACCOUNT_EMAIL;
  const password = process.env.E2E_ACCOUNT_PASSWORD;
  if (!email || !password) throw new Error("Missing isolated credentials");
  await page.goto("/account");
  await page.getByLabel("电子邮箱").fill(email);
  await page.getByLabel("密码（12–256 字符）").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator('.account-module-nav a[href="#audit"]').click();
  const audit = page.getByRole("region", { name: "空间审计记录" });
  await audit.getByRole("button", { name: "刷新审计记录" }).click();
  await expect(audit.getByRole("status")).toHaveText("已加载真实审计记录。");
  await expect(audit.getByRole("listitem").first()).toContainText("创建空间");
  await expect(audit.getByRole("listitem").first()).toContainText("操作者：");
  expect(await audit.textContent()).not.toContain(email);
  expect(await audit.textContent()).not.toContain(password);
});
