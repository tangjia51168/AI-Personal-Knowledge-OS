import { copyFile, readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("note revision proxy accepts only bounded POST on the exact note collection", async ({ page }) => {
  const root = "/api/account/spaces/00000000-0000-0000-0000-000000000001";
  const note = `${root}/notes/00000000-0000-0000-0000-000000000002`;
  expect((await page.request.get(`${note}/revisions`)).status()).toBe(405);
  expect((await page.request.delete(`${note}/revisions`)).status()).toBe(405);
  expect((await page.request.post(`${note}/revisions/extra`, { data: {} })).status()).toBe(404);
  expect((await page.request.post(`${note}/revisions`, { data: { personal_notes: "x".repeat(20 * 1024) } })).status()).toBe(401);
  expect((await page.request.post(`${note}/revisions`, { data: { personal_notes: "x".repeat(129 * 1024) } })).status()).toBe(413);
  expect((await page.request.post(`${root}/snapshots/00000000-0000-0000-0000-000000000002/summarize`, { data: { title: "x".repeat(5 * 1024) } })).status()).toBe(413);
});

test("manual revisions preserve history, retry the same key and retain drafts on concurrent conflicts without AI", async ({ page }) => {
  test.setTimeout(90000);
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated account opt-in required");
  const email = process.env.E2E_NOTE_EDIT_EMAIL;
  const password = process.env.E2E_NOTE_EDIT_PASSWORD;
  if (!email || !password) throw new Error("Missing isolated note edit protocol fixtures");
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
  await page.locator('.account-module-nav a[href="#knowledge"]').click();
  const knowledge = page.getByRole("region", { name: "知识笔记", exact: true });
  await knowledge.getByRole("button", { name: "刷新知识笔记", exact: true }).click();
  await knowledge.getByRole("button", { name: "打开笔记", exact: true }).click();
  const content = knowledge.getByRole("region", { name: "笔记内容", exact: true });
  await expect(content).toContainText("协议夹具，无模型调用，不能计为真实平台或模型验收");
  const versions = knowledge.getByLabel("笔记版本", { exact: true });
  const originalRevision = await versions.inputValue();
  const originalText = await content.locator("pre").textContent();
  const originalVersionCount = await versions.locator("option").count();
  // The base rows are clearly labeled protocol fixtures. Only the edit request
  // and persisted revision are real here; no inference or collection is implied.
  await knowledge.getByRole("button", { name: "开始人工修订", exact: true }).click();
  const editor = knowledge.getByRole("region", { name: "笔记人工修订", exact: true });
  const title = editor.getByLabel("修订标题", { exact: true });
  const overview = editor.getByLabel("修订概述", { exact: true });
  const personal = editor.getByLabel("个人理解（未经来源核验）", { exact: true });
  const save = editor.getByRole("button", { name: "保存为新版本", exact: true });
  const status = editor.getByRole("status", { name: "人工修订状态", exact: true });
  await expect(editor).toContainText("概述引用：E001");
  await expect(versions).toBeDisabled();
  await title.fill("浏览器人工修订（无模型调用）");
  await overview.fill("根据已有来源重新整理概述，内容仍需核对。");
  await personal.fill("个人理解：可以先检查输入条件，再观察结果。这是我的理解，未作来源核验。");

  await page.context().setOffline(true);
  const interrupted = page.waitForRequest(request => request.method() === "POST" && /\/notes\/[0-9a-f-]+\/revisions$/.test(new URL(request.url()).pathname));
  await save.click();
  const interruptedBody = (await interrupted).postDataJSON();
  await expect(status).toContainText("重试使用原幂等键");
  await expect(title).toHaveValue("浏览器人工修订（无模型调用）");
  await page.context().setOffline(false);
  const submitted = page.waitForRequest(request => request.method() === "POST" && /\/notes\/[0-9a-f-]+\/revisions$/.test(new URL(request.url()).pathname));
  await save.click();
  const request = await submitted;
  const body = request.postDataJSON();
  expect(body).toEqual(interruptedBody);
  expect(body.base_revision_id).toBe(originalRevision);
  expect(body.overview.evidence_keys).toEqual(["E001"]);
  expect(body).not.toHaveProperty("content_md");
  expect(body).not.toHaveProperty("limitations");
  await expect(editor).not.toBeVisible();
  await expect(content).toContainText("人工修订；本版本未调用模型");
  await expect(content).toContainText("个人理解：可以先检查输入条件");
  await expect(content).toContainText("未经来源核验");
  await expect(content).toContainText("### E001");
  await expect(versions.locator("option")).toHaveCount(originalVersionCount + 1);
  const editedRevision = await versions.inputValue();
  const authorization = await request.headerValue("x-knowledge-request");
  if (!authorization) throw new Error("Missing authenticated revision request");
  const replay = await page.request.post(request.url(), { headers: { "X-Knowledge-Request": authorization }, data: body });
  expect(replay.ok()).toBe(true);
  expect((await replay.json()).data.revision_id).toBe(editedRevision);

  const downloaded = page.waitForEvent("download");
  await content.getByRole("button", { name: "下载 Markdown", exact: true }).click();
  const file = await (await downloaded).path();
  if (!file) throw new Error("Missing local edited Markdown download");
  const markdown = await readFile(file, "utf8");
  expect(markdown).toContain("浏览器人工修订（无模型调用）");
  expect(markdown).toContain("个人理解：可以先检查输入条件");
  expect(markdown).toContain("个人理解未作来源或事实核验");
  expect(markdown).toContain("### E001");
  for (const [label, suffix] of [["JSON", "json"], ["HTML", "html"], ["纯文本", "txt"]]) {
    const nextDownload = page.waitForEvent("download");
    await content.getByRole("button", { name: `下载 ${label}`, exact: true }).click();
    const artifact = await nextDownload;
    expect(artifact.suggestedFilename()).toBe(`knowledge-${editedRevision}.${suffix}`);
    const artifactPath = await artifact.path();
    if (!artifactPath) throw new Error("Missing exported artifact");
    const exported = await readFile(artifactPath, "utf8");
    expect(exported).toContain("个人理解：可以先检查输入条件");
    expect(exported).toContain("个人理解未作来源或事实核验");
    expect(exported).toContain("E001");
    expect(exported).toContain(suffix === "json" ? "original_url" : "原链接");
    if (suffix === "json") {
      const data = JSON.parse(exported);
      expect(data.revision.id).toBe(editedRevision);
      expect(data.revision.origin).toBe("user_edit");
      expect(data.revision.content_md).toBe(markdown);
    }
    if (suffix === "html") {
      expect(exported).not.toContain("<script");
      expect(exported).toContain("default-src 'none'");
      const preview = await page.context().newPage();
      const remoteRequests: string[] = [];
      preview.on("request", request => { if (/^https?:/.test(request.url())) remoteRequests.push(request.url()); });
      const previewPath = test.info().outputPath("note-export.html");
      await copyFile(artifactPath, previewPath);
      await preview.goto(pathToFileURL(previewPath).href);
      await expect(preview.locator("pre")).toContainText("个人理解：可以先检查输入条件");
      await expect(preview.locator("script, img, iframe, a")).toHaveCount(0);
      expect(remoteRequests).toEqual([]);
      await preview.close();
    }
    await expect(content.getByRole("status", { name: "笔记导出状态" })).toContainText("已准备当前修订");
  }
  await versions.selectOption(originalRevision);
  await expect(content.locator("pre")).toHaveText(originalText ?? "");
  await page.context().setOffline(true);
  await content.getByRole("button", { name: "下载 JSON", exact: true }).click();
  await expect(content.getByRole("status", { name: "笔记导出状态" })).toContainText("未生成下载文件");
  await page.context().setOffline(false);
  const historicalDownload = page.waitForEvent("download");
  await content.getByRole("button", { name: "下载 JSON", exact: true }).click();
  const historicalFile = await (await historicalDownload).path();
  if (!historicalFile) throw new Error("Missing historical JSON export");
  const historical = JSON.parse(await readFile(historicalFile, "utf8"));
  expect(historical.revision.id).toBe(originalRevision);
  expect(historical.revision.content_md).toBe(originalText);
  expect(historical.revision.document).not.toHaveProperty("personal_notes");
  await expect(knowledge.getByRole("button", { name: "开始人工修订", exact: true })).toHaveCount(0);
  await knowledge.getByRole("button", { name: "打开最新版本", exact: true }).click();
  await expect(versions).toHaveValue(editedRevision);

  // A second real client wins the race after this editor has loaded its base.
  await knowledge.getByRole("button", { name: "开始人工修订", exact: true }).click();
  await title.fill("尚未保存的并发草稿");
  const external = await page.request.post(request.url(), {
    headers: { "X-Knowledge-Request": authorization },
    data: { ...body, idempotency_key: crypto.randomUUID(), base_revision_id: editedRevision, title: "另一客户端已提交的人工修订" },
  });
  expect(external.ok()).toBe(true);
  const externalRevision = (await external.json()).data.revision_id;
  const externalRead = await page.request.get(`${request.url()}/${externalRevision}`, { headers: { "X-Knowledge-Request": authorization } });
  expect(externalRead.ok()).toBe(true);
  const externalTitle = (await externalRead.json()).data.title;
  await save.click();
  await expect(status).toContainText("已有更新版本，本次未保存。草稿已保留");
  await expect(title).toHaveValue("尚未保存的并发草稿");
  await expect(save).toBeDisabled();
  await editor.getByRole("button", { name: "放弃草稿并打开最新版本", exact: true }).click();
  await expect(editor).not.toBeVisible();
  await expect(versions).toHaveValue(externalRevision);
  await expect(content).toContainText("另一客户端已提交的人工修订");

  // A changed draft gets a new key; leaving the editor explicitly discards it.
  await knowledge.getByRole("button", { name: "开始人工修订", exact: true }).click();
  await title.fill("仅留在内存中的草稿");
  await page.context().setOffline(true);
  const changedFirst = page.waitForRequest(candidate => candidate.method() === "POST" && candidate.url() === request.url());
  await save.click();
  const changedFirstBody = (await changedFirst).postDataJSON();
  await expect(status).toContainText("重试使用原幂等键");
  await title.fill("编辑后仅留在内存中的草稿");
  const changedSecond = page.waitForRequest(candidate => candidate.method() === "POST" && candidate.url() === request.url());
  await save.click();
  const changedSecondBody = (await changedSecond).postDataJSON();
  expect(changedSecondBody.idempotency_key).not.toBe(changedFirstBody.idempotency_key);
  expect(changedSecondBody.base_revision_id).toBe(changedFirstBody.base_revision_id);
  await expect(status).toContainText("重试使用原幂等键");
  await page.context().setOffline(false);
  await editor.getByRole("button", { name: "放弃草稿", exact: true }).click();
  await knowledge.getByRole("button", { name: "开始人工修订", exact: true }).click();
  await expect(title).toHaveValue(externalTitle);
  await editor.getByRole("button", { name: "放弃草稿", exact: true }).click();
  await page.locator('.account-module-nav a[href="#audit"]').click();
  const audit = page.getByRole("region", { name: "空间审计记录", exact: true });
  await audit.getByRole("button", { name: "刷新审计记录", exact: true }).click();
  await expect(audit.getByRole("status")).toHaveText("已加载真实审计记录。");
  await expect(audit.getByRole("listitem").filter({ hasText: "人工修订笔记" })).toHaveCount(2);
  await expect(audit).not.toContainText("notes.edited");
  expect(paidPosts).toBe(0);
  expect(collectionPosts).toBe(0);
});
