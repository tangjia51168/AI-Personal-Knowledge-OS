import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("import proxy allows only bounded POST on the exact authorized source path", async ({ page }) => {
  const root = "/api/account/spaces/00000000-0000-0000-0000-000000000001";
  const source = `${root}/sources/00000000-0000-0000-0000-000000000002`;
  expect((await page.request.get(`${source}/imports`)).status()).toBe(405);
  expect((await page.request.delete(`${source}/imports`)).status()).toBe(405);
  expect((await page.request.post(`${source}/imports/extra`, { data: {} })).status()).toBe(404);
  expect((await page.request.post(`${source}/imports`, { data: { text: "x".repeat(20 * 1024) } })).status()).toBe(401);
  expect((await page.request.post(`${source}/imports`, { data: { text: "x".repeat(129 * 1024) } })).status()).toBe(413);
  expect((await page.request.post(`${source}/collect`, { data: { text: "x".repeat(5 * 1024) } })).status()).toBe(413);
});

test("authorized subtitles survive a network retry and become a real Worker import snapshot without collection or AI", async ({ page }) => {
  test.setTimeout(90000);
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated account opt-in required");
  const email = process.env.E2E_KNOWLEDGE_EMAIL;
  const password = process.env.E2E_KNOWLEDGE_PASSWORD;
  if (!email || !password) throw new Error("Missing isolated knowledge credentials");
  let paidPosts = 0;
  let collectionPosts = 0;
  page.on("request", request => {
    if (request.method() !== "POST") return;
    if (/\/summarize$/.test(new URL(request.url()).pathname)) paidPosts++;
    if (/\/collect$/.test(new URL(request.url()).pathname)) collectionPosts++;
  });
  await page.goto("/account");
  await page.getByLabel("电子邮箱").fill(email);
  await page.getByLabel("密码（12–256 字符）").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator('.account-module-nav a[href="#inbox"]').click();
  const sources = page.getByRole("region", { name: "已保存来源候选", exact: true });
  await sources.getByRole("button", { name: "刷新已保存候选" }).click();
  await sources.getByText("导入授权正文或字幕", { exact: true }).click();
  const form = sources.getByRole("region", { name: "授权正文与字幕导入", exact: true });
  const title = form.getByLabel("导入标题", { exact: true });
  const text = form.getByLabel("授权正文或字幕", { exact: true });
  const consent = form.getByLabel("我有权使用这些内容，并同意保存人工导入快照");
  const submit = form.getByRole("button", { name: "授权并导入内容", exact: true });
  const status = form.getByRole("status", { name: "正文导入状态", exact: true });

  await title.fill("浏览器人工字幕导入（非平台采集）");
  await text.fill("汉".repeat(21846));
  await expect(submit).toBeDisabled();
  await consent.check();
  await submit.click();
  await expect(status).toContainText("64KiB UTF-8");
  await text.fill("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n先连接输入电源。\n\n00:00:03.000 --> 00:00:06.000\n检查输出电压后再记录结果。\n");
  await expect(consent).not.toBeChecked();
  await form.getByLabel("导入格式", { exact: true }).selectOption("vtt");
  await expect(form.getByLabel("内容类型", { exact: true })).toHaveValue("transcript");
  await expect(form.getByLabel("内容类型", { exact: true })).toBeDisabled();
  await consent.check();

  await page.context().setOffline(true);
  const interrupted = page.waitForRequest(request => request.method() === "POST" && /\/imports$/.test(new URL(request.url()).pathname));
  await submit.click();
  const interruptedBody = (await interrupted).postDataJSON();
  await expect(submit).toBeEnabled();
  await expect(status).toContainText("重试保留原幂等键");
  await page.context().setOffline(false);
  const submitted = page.waitForRequest(request => request.method() === "POST" && /\/imports$/.test(new URL(request.url()).pathname));
  await submit.click();
  const request = await submitted;
  const body = request.postDataJSON();
  expect(body).toEqual(interruptedBody);
  expect(body.format).toBe("vtt");
  expect(body.content_kind).toBe("transcript");
  await expect(status).toContainText("导入任务已保存");
  await expect(submit).toBeDisabled();
  await expect(text).toBeDisabled();

  const refresh = form.getByRole("button", { name: "刷新导入状态与快照", exact: true });
  await expect.poll(async () => {
    await refresh.click();
    await expect(refresh).toBeEnabled();
    return status.textContent();
  }, { timeout: 45000, intervals: [1000, 2000, 4000] }).toContain("人工导入快照已保存");
  await expect(sources).toContainText("运维尚未启用");
  const item = sources.getByRole("listitem").filter({ hasText: "浏览器人工字幕导入（非平台采集）" }).filter({ has: page.getByRole("button", { name: /^打开快照版本/ }) }).last();
  await expect(item).toContainText("人工导入（未核验）");
  const open = item.getByRole("button", { name: /^打开快照版本/ }).last();
  await open.click();
  const snapshot = sources.getByRole("region", { name: "来源快照", exact: true });
  await expect(snapshot).toContainText("人工导入（未核验）");
  await expect(snapshot).toContainText("导入时未访问该地址");
  await expect(snapshot).toContainText("00:00:01.000");
  await expect(snapshot).toContainText("先连接输入电源。");
  await expect(snapshot).toContainText("尚未核验与关联平台内容是否一致");

  const authorization = await request.headerValue("x-knowledge-request");
  if (!authorization) throw new Error("Missing authenticated import request");
  const replay = await page.request.post(request.url(), { headers: { "X-Knowledge-Request": authorization }, data: body });
  expect(replay.status()).toBe(202);
  const result = (await replay.json()).data;
  await expect(form).toContainText(`导入任务：${result.id}`);
  expect(paidPosts).toBe(0);
  expect(collectionPosts).toBe(0);
});
