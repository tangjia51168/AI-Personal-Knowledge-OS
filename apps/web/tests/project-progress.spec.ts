import { expect, test } from "@playwright/test";
import { currentTrack, moduleResults, trackModules, tracks } from "../src/lib/project-progress";
import { requirementStateNames, trackAcceptance, trackVerification } from "../src/lib/track-acceptance";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("project progress shows versioned evidence and real authenticated entry points without tenant reads", async ({ page }) => {
  const requests: string[] = [];
  const errors: string[] = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.method());
  });
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/progress");
  await expect(page.getByRole("heading", { name: "项目进度与模块成果", exact: true })).toBeVisible();
  await expect(page.getByLabel("项目主线筛选结果")).toHaveText(`显示 ${tracks.length} / ${tracks.length} 条主线。当前模块通过不等于整条产品主线完成。`);
  await expect(page.getByLabel("进度记录边界")).toContainText("不是实时业务数据");
  await expect(page.getByLabel("进度记录边界")).toContainText("当前不能公开上线");
  const filter = page.getByLabel("按状态查看", { exact: true });
  await filter.selectOption("planned");
  await expect(page.locator(".progress-track")).toHaveCount(tracks.filter(track => track.status === "planned").length);
  await expect(page.locator(".progress-track")).toHaveCount(0);
  await filter.selectOption("deferred");
  await expect(page.locator(".progress-track")).toHaveCount(tracks.filter(track => track.status === "deferred").length);
  await filter.selectOption("all");
  await expect(page.locator(".progress-track")).toHaveCount(tracks.length);
  await expect(page.locator(".progress-module")).toHaveCount(moduleResults.length);
  await expect(page.getByRole("region", { name: "当前实施主线", exact: true })).toContainText(currentTrack.name);
  const moduleFilter = page.getByLabel("按项目主线查看成果", { exact: true });
  await moduleFilter.selectOption("organization");
  await expect(page.locator(".progress-module")).toHaveCount(trackModules.organization.length);
  await moduleFilter.selectOption("rag");
  await expect(page.locator(".progress-module")).toHaveCount(trackModules.rag.length);
  await expect(page.getByRole("status", { name: "主线成果筛选结果", exact: true })).toContainText(`显示 ${trackModules.rag.length} 项`);
  await moduleFilter.selectOption("all");
  for (const item of moduleResults) {
    const card = page.locator(`#module-${item.id}`);
    await card.getByText("查看结果与验收边界", { exact: true }).click();
    await expect(card).toContainText(item.checks);
    await expect(card).toContainText(item.limitation);
    await expect(card.getByRole("link")).toHaveAttribute("href", `/account#${item.target}`);
    if (item.id === "learning-summary") await card.screenshot({ path: ".cache/module-results/learning-summary-delivery-card.png" });
    if (item.id === "learning-review") await card.screenshot({ path: ".cache/module-results/learning-review-delivery-card.png" });
    if (item.id === "learning-schedule") await card.screenshot({ path: ".cache/module-results/learning-schedule-delivery-card.png" });
    if (item.id === "reminder-batch") await card.screenshot({ path: ".cache/module-results/reminder-batch-delivery-card.png" });
    if (item.id === "review-plan") await card.screenshot({ path: ".cache/module-results/review-plan-delivery-card.png" });
    if (item.id === "review-workbench") await card.screenshot({ path: ".cache/module-results/review-workbench-delivery-card.png" });
    if (item.id === "learning-analysis") await card.screenshot({ path: ".cache/module-results/learning-analysis-delivery-card.png" });
    if (item.id === "label-management") await card.screenshot({ path: ".cache/module-results/label-management-delivery-card.png" });
    if (item.id === "organization-workspace") await card.screenshot({ path: ".cache/module-results/organization-track-delivery-card.png" });
    if (item.id === "mainline-acceptance") await card.screenshot({ path: ".cache/module-results/mainline-acceptance-delivery-card.png" });
    if (item.id === "topic-hierarchy") await card.screenshot({ path: ".cache/module-results/topic-hierarchy-delivery-card.png" });
    if (item.id === "concept-cards") await card.screenshot({ path: ".cache/module-results/concept-cards-delivery-card.png" });
    if (item.id === "foundation-recovery") await card.screenshot({ path: ".cache/module-results/foundation-recovery-delivery-card.png" });
    if (item.id === "v-one-workspace") await card.screenshot({ path: ".cache/module-results/v1-delivery-card.png" });
  }
  await page.reload();
  await expect(page.getByRole("heading", { name: "项目进度与模块成果", exact: true })).toBeVisible();
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
  await page.getByRole("link", { name: "打开实际功能", exact: false }).click();
  await expect(page.getByRole("button", { name: "登录", exact: true })).toBeVisible();
});

test("progress entry, hash links, mobile layout and screenshot use the actual build", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "项目进度与成果", exact: true }).click();
  await expect(page).toHaveURL(/\/progress$/);
  await page.goto("/progress#module-local-graph");
  await expect(page.getByRole("heading", { name: "人工链接局部图谱", exact: true })).toBeInViewport();
  await page.goto("/progress#module-organization-workspace");
  await expect(page.getByRole("heading", { name: "知识组织与检索主线工作区", exact: true })).toBeInViewport();
  await page.goto("/progress");
  await page.screenshot({ path: ".cache/module-results/project-progress-overview.png" });
  await page.screenshot({ path: ".cache/module-results/project-progress-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("按状态查看", { exact: true }).selectOption("partial");
  await expect(page.locator(".progress-track")).toHaveCount(tracks.filter(track => track.status === "partial").length);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: ".cache/module-results/project-progress-mobile.png", fullPage: true });
});

test("all twelve tracks expose complete acceptance gaps and preserve unresolved references", async ({ page }) => {
  let calls = 0;
  page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) calls++; });
  await page.goto("/progress");
  const checklist = page.getByRole("region", { name: "主线需求与验收清单", exact: true });
  const select = checklist.getByLabel("选择验收主线", { exact: true });
  for (const track of trackAcceptance) {
    await select.selectOption(track.track);
    await expect(checklist.getByRole("heading", { level: 3 })).toContainText("整体未验收");
    await checklist.getByText("本轮已实现范围的验收结果", { exact: true }).click();
    await expect(checklist).toContainText(trackVerification[track.track].integration);
    await expect(checklist).toContainText(trackVerification[track.track].remaining);
    await checklist.getByText("本轮已实现范围的验收结果", { exact: true }).click();
    const items = checklist.getByRole("list", { name: "主线验收项", exact: true }).locator(":scope > li");
    await expect(items).toHaveCount(track.requirements.length);
    for (const requirement of track.requirements) {
      const item = checklist.locator(`#${requirement.id}`);
      await expect(item).toContainText(requirement.acceptance);
      await expect(item).toContainText(requirement.gap);
      await expect(item).toContainText(requirementStateNames[requirement.state]);
    }
  }
  await select.selectOption("rag");
  await checklist.getByLabel("验收项状态", { exact: true }).selectOption("recorded");
  await expect(checklist).toContainText("当前状态没有验收项");
  await select.selectOption("organization");
  await expect(checklist.getByRole("listitem")).toHaveCount(4);
  await checklist.getByText("参考资料与核实情况", { exact: true }).click();
  await expect(checklist).toContainText("Bookmark 文档已作为两条新增对话的需求来源读取");
  await expect(checklist).toContainText("1992");
  await checklist.screenshot({ path: ".cache/module-results/mainline-acceptance-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await checklist.screenshot({ path: ".cache/module-results/mainline-acceptance-mobile.png" });
  expect(calls).toBe(0);
});
