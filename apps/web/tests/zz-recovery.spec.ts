import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("single-use email links clear fragments and require confirmation before a real API POST", async ({ page }) => {
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated account opt-in required");
  // Traces would capture the fixture's one-time bearer fragment and POST body.
  const verification = process.env.E2E_VERIFY_TOKEN;
  const reset = process.env.E2E_RESET_TOKEN;
  const password = process.env.E2E_ACCOUNT_PASSWORD;
  if (!verification || !reset || !password) throw new Error("Missing isolated recovery fixtures");
  let actionPosts = 0;
  page.on("request", request => {
    if (/\/(verify-email|reset-password)$/.test(new URL(request.url()).pathname)) actionPosts++;
  });
  await page.goto(`/account#verify=${verification}`);
  const recovery = page.getByRole("region", { name: "邮箱验证与找回密码" });
  await expect(recovery.getByRole("button", { name: "确认验证邮箱" })).toBeVisible();
  expect(new URL(page.url()).hash).toBe("");
  expect(actionPosts).toBe(0);
  await recovery.getByRole("button", { name: "确认验证邮箱" }).click();
  await expect(recovery.getByRole("status")).toHaveText("邮箱已验证，请使用密码登录。");
  expect(actionPosts).toBe(1);
  await page.goto(`/account#reset=${reset}`);
  await expect(recovery.getByLabel("新密码")).toBeVisible();
  expect(new URL(page.url()).hash).toBe("");
  await recovery.getByLabel("新密码").fill(password);
  await recovery.getByRole("button", { name: "确认重置密码" }).click();
  await expect(recovery.getByRole("status")).toContainText("所有旧会话已撤销");
  expect(actionPosts).toBe(2);
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(reset);
});
