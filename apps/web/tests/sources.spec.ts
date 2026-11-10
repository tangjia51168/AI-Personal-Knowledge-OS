import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("source proxy rejects anonymous access, unsupported methods and oversized JSON", async ({ page }) => {
  const path = "/api/account/spaces/00000000-0000-0000-0000-000000000001/sources";
  expect((await page.request.get(path)).status()).toBe(401);
  expect((await page.request.delete(path)).status()).toBe(405);
  expect((await page.request.get(`${path}/inspect`)).status()).toBe(405);
  expect((await page.request.post(path, { data: { text: "x".repeat(18 * 1024) } })).status()).toBe(413);
});

test("real source candidates are inspected, saved, deduplicated and listed without fetching", async ({ page }) => {
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated account opt-in required");
  const email = process.env.E2E_ACCOUNT_EMAIL;
  const password = process.env.E2E_ACCOUNT_PASSWORD;
  if (!email || !password) throw new Error("Missing isolated credentials");
  await page.goto("/account");
  await page.getByLabel("电子邮箱").fill(email);
  await page.getByLabel("密码（12–256 字符）").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator('.account-module-nav a[href="#inbox"]').click();
  const inbox = page.getByRole("region", { name: "内容收件箱", exact: true });
  await expect(inbox).toBeVisible();
  const input = inbox.getByLabel("分享文本");
  const inspect = inbox.getByRole("button", { name: "检查链接", exact: true });
  const save = inbox.getByRole("button", { name: "保存本批候选", exact: true });
  const status = inbox.getByRole("status", { name: "来源操作状态", exact: true });
  const base = `https://example.com/browser-source-${Date.now()}`;

  await input.fill(`${base}#old`);
  await inspect.click();
  await expect(save).toBeEnabled();
  await input.fill(`${base}#new http://127.0.0.1/private`);
  await expect(save).toHaveCount(0);
  await expect(inbox.getByText(`原始：${base}#old`, { exact: true })).toHaveCount(0);
  await inspect.click();
  await expect(save).toBeEnabled();
  await expect(inbox).toContainText("PRIVATE_IP");
  expect(await inbox.locator("a").count()).toBe(0);

  // Use a real browser network failure, then retry the same checked batch.
  await page.context().setOffline(true);
  const interruptedRequest = page.waitForRequest(request => request.method() === "POST" && /\/sources$/.test(new URL(request.url()).pathname));
  await save.click();
  const interruptedBody = (await interruptedRequest).postDataJSON();
  await expect(save).toBeEnabled();
  await expect(status).not.toBeEmpty();
  await page.context().setOffline(false);
  const firstRequest = page.waitForRequest(request => request.method() === "POST" && /\/sources$/.test(new URL(request.url()).pathname));
  await save.click();
  const request = await firstRequest;
  const body = request.postDataJSON();
  expect(body).toEqual(interruptedBody);
  expect(body.text).toBe(`${base}#new http://127.0.0.1/private`);
  expect(body.idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
  await expect(status).toContainText("接受 1，拒绝 1");
  await expect(save).toBeDisabled();

  const saved = inbox.getByRole("region", { name: "已保存来源候选", exact: true });
  await saved.getByRole("button", { name: "刷新已保存候选" }).click();
  await expect(saved.getByRole("listitem")).toHaveCount(2);
  await expect(saved).toContainText("URL 候选已入库");
  await expect(saved).toContainText(`${base}#new`);
  await expect(saved).toContainText("[blocked]");
  expect(await saved.textContent()).not.toContain("http://127.0.0.1/private");

  // Replaying the actual successful request still uses the real BFF and DB.
  const authorization = await request.headerValue("x-knowledge-request");
  if (!authorization) throw new Error("Missing authenticated source request");
  const replay = await page.request.post(request.url(), { headers: { "X-Knowledge-Request": authorization }, data: body });
  expect(replay.status()).toBe(201);
  const replayed = (await replay.json()).data;
  expect(replayed.accepted_count).toBe(1);
  expect(replayed.rejected_count).toBe(1);
  expect(replayed.sources).toHaveLength(2);
  await saved.getByRole("button", { name: "刷新已保存候选" }).click();
  await expect(saved.getByRole("listitem")).toHaveCount(2);

  await input.fill(`${base}#duplicate`);
  await inspect.click();
  await expect(save).toBeEnabled();
  await save.click();
  await expect(status).toContainText("接受 0，拒绝 1");
  await saved.getByRole("button", { name: "刷新已保存候选" }).click();
  await expect(saved.getByRole("listitem")).toHaveCount(3);
  await expect(saved).toContainText("DUPLICATE_CANONICAL_URL");

  await page.locator('.account-module-nav a[href="#audit"]').click();
  const audit = page.getByRole("region", { name: "空间审计记录" });
  await audit.getByRole("button", { name: "刷新审计记录" }).click();
  await expect(audit).toContainText("保存来源候选批次");
  await page.locator('.account-module-nav a[href="#inbox"]').click();

  await input.fill(Array.from({ length: 20 }, (_, index) => `${base}/${index}`).join(" "));
  await inspect.click();
  await expect(save).toBeEnabled();
  await expect(inbox).toContainText("发现 20 个候选。");
  await input.fill(Array.from({ length: 21 }, (_, index) => `${base}/${index}`).join(" "));
  await inspect.click();
  await expect(save).toBeDisabled();
  await expect(inbox).toContainText("超过上限，仅展示前 20 个");

  await input.fill("汉".repeat(5462));
  await inspect.click();
  await expect(status).toContainText("16KiB UTF-8 上限");
  await expect(save).toHaveCount(0);

  await input.fill("没有链接的分享文本");
  await inspect.click();
  await expect(inbox).toContainText("未发现可识别的 URL");
  await expect(save).toHaveCount(0);
});
