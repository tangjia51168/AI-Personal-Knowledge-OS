import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("semantic BFF restricts methods, parameters and authentication", async ({ page }) => {
  const root = "/api/account/spaces/00000000-0000-0000-0000-000000000001/knowledge/semantic";
  expect((await page.request.get(`${root}/search?q=账号`)).status()).toBe(401);
  expect((await page.request.post(`${root}/search`)).status()).toBe(405);
  expect((await page.request.get(`${root}/index`)).status()).toBe(405);
  expect((await page.request.get(`${root}/search?q=a&q=b`)).status()).toBe(422);
  expect((await page.request.get(`${root}/search?q=a&tenant_id=x`)).status()).toBe(422);
  expect((await page.request.post(`${root}/index?user_id=x`)).status()).toBe(422);
});

test("real local indexing, semantic search, fixed revision and failure clearing", async ({ page }) => {
  test.setTimeout(150000);
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated accounts required");
  const email = process.env.E2E_EXPLORER_EMAIL, password = process.env.E2E_EXPLORER_PASSWORD;
  if (!email || !password) throw new Error("Missing isolated fixture");
  await page.goto("/account#knowledge");
  await page.getByLabel("电子邮箱", { exact: true }).fill(email);
  await page.getByLabel("密码（12–256 字符）", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const knowledge = page.getByRole("region", { name: "知识笔记", exact: true });
  await knowledge.getByRole("button", { name: "刷新知识笔记", exact: true }).click();
  await knowledge.getByRole("button", { name: "打开笔记", exact: true }).first().click();
  const detail = knowledge.getByRole("region", { name: "笔记内容", exact: true }).first();
  await detail.getByText("建立本地语义检索索引", { exact: true }).click();
  const index = detail.getByRole("region", { name: "本地向量索引", exact: true });
  await index.getByLabel("同意为当前固定修订建立本地向量索引", { exact: true }).check();
  await index.getByRole("button", { name: "建立当前修订索引", exact: true }).click();
  await expect(index.getByRole("status", { name: "本地索引状态", exact: true })).toContainText("已保存", { timeout: 90000 });
  await index.getByLabel("同意为当前固定修订建立本地向量索引", { exact: true }).check();
  await index.getByRole("button", { name: "建立当前修订索引", exact: true }).click();
  await expect(index.getByRole("status", { name: "本地索引状态", exact: true })).toContainText("已复用");
  const search = page.getByRole("region", { name: "本地语义检索", exact: true });
  await search.getByLabel("语义查询", { exact: true }).fill("只有标题和简介，没有字幕");
  await search.getByRole("button", { name: "按含义检索", exact: true }).click();
  await expect(search.getByRole("status", { name: "语义检索状态", exact: true })).toContainText("找到 1 篇", { timeout: 90000 });
  await expect(search).toContainText("最新修订索引覆盖：1 / 2 篇");
  await search.getByRole("button", { name: "打开语义命中修订", exact: true }).click();
  await expect(search.getByRole("region", { name: "笔记内容", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: ".cache/module-results/semantic-search-mobile.png", fullPage: true });
  await page.route("**/knowledge/semantic/search?*", route => route.abort());
  await search.getByRole("button", { name: "按含义检索", exact: true }).click();
  await expect(search.getByRole("status", { name: "语义检索状态", exact: true })).toContainText("已清空旧结果");
  await expect(search.getByRole("list", { name: "语义检索结果", exact: true })).toHaveCount(0);
});
