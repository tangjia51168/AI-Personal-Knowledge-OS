import { expect, test } from "@playwright/test";

test("real backend status, refresh and honest feature boundaries", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "让收藏，成为真正的知识。" })).toBeVisible();
  await expect(page.getByRole("link", { name: "登录使用知识工作区" })).toHaveAttribute("href", "/account");
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "证据研究与问答" })).toHaveAttribute("href", "/account#research");
  await expect(page.getByRole("button", { name: "刷新状态" })).toBeEnabled({ timeout: 20000 });
  const actual = await page.request.get("/api/system");
  const data = (await actual.json()).data;
  const apiCard = page.locator(".service-card").filter({ hasText: "API 服务" });
  await expect(apiCard).toContainText(data.api.status === "up" ? "已连接" : "未连接");
  for (const [key, title] of [["postgresql", "知识数据库"], ["redis", "消息队列"], ["storage", "对象存储"]]) {
    const card = page.locator(".service-card").filter({ hasText: title });
    await expect(card).toContainText(data.dependencies?.[key]?.status === "up" ? "已连接" : "未连接");
  }
  await page.screenshot({ path: ".cache/module-results/v1-home.png", fullPage: true });
  await page.getByRole("button", { name: "刷新状态" }).click();
  await expect(page.getByRole("button", { name: "刷新状态" })).toBeEnabled({ timeout: 20000 });
  await page.getByRole("button", { name: "内容收件箱", exact: true }).click();
  await expect(page.getByRole("heading", { name: "检查并保存分享链接" })).toBeVisible();
  await expect(page.getByRole("link", { name: "打开账户收件箱" })).toHaveAttribute("href", "/account");
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await expect(page.getByRole("link", { name: "打开账户与任务中心" })).toHaveAttribute("href", "/account");
  expect(errors).toEqual([]);
});

test("theme persists and setup guide opens and closes", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", /light|dark/);
  await page.getByRole("button", { name: "系统设置", exact: true }).click();
  const before = await page.locator("html").getAttribute("data-theme");
  await page.locator(".theme-option").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", before === "dark" ? "light" : "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", before === "dark" ? "light" : "dark");
  await page.getByRole("button", { name: "查看启动步骤" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("docker compose up --build -d", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "知道了" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
});

test("mobile navigation works without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("button", { name: "系统设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "外观设置" })).toBeVisible();
  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(fits).toBe(true);
});
