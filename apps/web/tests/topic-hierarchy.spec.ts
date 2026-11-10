import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("real topic parent selection, archive preservation, root move and offline reconciliation", async ({ page }) => {
  test.setTimeout(120000);
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated account opt-in required");
  const email = process.env.E2E_HIERARCHY_EMAIL;
  const password = process.env.E2E_HIERARCHY_PASSWORD;
  const labels = process.env.E2E_HIERARCHY_LABELS
    ? (JSON.parse(process.env.E2E_HIERARCHY_LABELS) as Record<string, string>)
    : null;
  if (!email || !password || !labels) throw new Error("Missing isolated hierarchy account");
  let externalPosts = 0;
  page.on("request", request => {
    if (request.method() === "POST" && /\/(summarize|collect)$/.test(new URL(request.url()).pathname)) externalPosts++;
  });
  const origin = process.env.E2E_HIERARCHY_BASE_URL ?? process.env.E2E_BASE_URL;
  if (!origin) throw new Error("Missing isolated hierarchy Web origin");
  await page.goto(new URL("/account", origin).href);
  await page.getByLabel("电子邮箱").fill(email);
  await page.getByLabel("密码（12–256 字符）").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator('.account-module-nav a[href="#knowledge"]').click();
  const manager = page.getByRole("region", { name: "分类目录维护", exact: true });
  const refresh = manager.getByRole("button", { name: "刷新分类目录", exact: true });
  const status = manager.getByRole("status");
  const loading = page.waitForRequest(request => request.method() === "GET" && /\/labels\/manage$/.test(new URL(request.url()).pathname));
  await refresh.click();
  const request = await loading;
  const authorization = await request.headerValue("x-knowledge-request");
  if (!authorization) throw new Error("Missing authenticated catalog request");
  const root = labels["主题基础"];
  const child = labels["电路实践"];
  const leaf = labels["实验记录"];
  const tag = labels["参考标签"];
  const choose = manager.getByRole("combobox", { name: "选择维护分类", exact: true });
  const parent = manager.getByRole("combobox", { name: "上级主题", exact: true });
  const confirm = manager.getByLabel("确认更新这个共享分类，已有笔记关联保留", { exact: true });
  const save = manager.getByRole("button", { name: "保存分类维护", exact: true });
  async function reload() {
    await refresh.click();
    await expect(status).toHaveText("已加载可维护的分类目录。");
  }
  async function persist() {
    await confirm.check();
    await save.click();
    await expect(status).toContainText("分类维护已保存");
    await reload();
  }
  await reload();
  const inventory = manager.getByRole("list", { name: "可维护分类", exact: true });
  await expect(inventory).toContainText("主题基础 / 电路实践 / 实验记录");
  await choose.selectOption(root);
  await expect(parent.locator(`option[value="${root}"]`)).toHaveCount(0);
  await expect(parent.locator(`option[value="${child}"]`)).toHaveCount(0);
  await expect(parent.locator(`option[value="${leaf}"]`)).toHaveCount(0);
  await expect(parent.locator(`option[value="${tag}"]`)).toHaveCount(0);
  await manager.getByLabel("允许新的笔记关联（取消为归档）", { exact: true }).uncheck();
  await persist();
  await choose.selectOption(child);
  await expect(parent).toHaveValue(root);
  await expect(parent.locator(`option[value="${root}"]`)).toHaveAttribute("disabled", "");
  await manager.getByLabel("维护后的分类名称", { exact: true }).fill("电路维护后");
  await persist();
  await expect(inventory).toContainText("主题基础（已归档） / 电路维护后 / 实验记录");
  await choose.selectOption(leaf);
  await expect(parent.locator(`option[value="${root}"]`)).toHaveCount(0);
  await choose.selectOption(tag);
  await expect(parent).not.toBeVisible();
  await choose.selectOption(child);
  await confirm.check();
  await parent.selectOption("");
  await expect(confirm).not.toBeChecked();
  await persist();
  await expect(inventory).toContainText("主题：电路维护后 · 启用");
  await expect(inventory).toContainText("电路维护后 / 实验记录");
  await choose.selectOption(leaf);
  await parent.selectOption("");
  await persist();
  await choose.selectOption(leaf);
  await parent.selectOption(child);
  await persist();
  await expect(inventory).toContainText("电路维护后 / 实验记录");
  await choose.selectOption(leaf);
  await parent.selectOption("");
  await confirm.check();
  await page.context().setOffline(true);
  await save.click();
  await expect(status).toContainText("保存结果可能未知");
  await expect(choose).not.toBeVisible();
  await page.context().setOffline(false);
  await reload();
  await expect(inventory).toContainText("电路维护后 / 实验记录");
  await manager.screenshot({ path: ".cache/module-results/topic-hierarchy-protocol.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await manager.screenshot({ path: ".cache/module-results/topic-hierarchy-mobile-protocol.png" });
  expect(externalPosts).toBe(0);
});
