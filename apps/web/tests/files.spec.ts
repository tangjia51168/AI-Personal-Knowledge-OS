import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("real private file can upload download and delete", async ({ page }) => {
  test.skip(process.env.RUN_ACCOUNT_E2E !== "1", "Explicit isolated account opt-in required");
  const email = process.env.E2E_ACCOUNT_EMAIL;
  const password = process.env.E2E_ACCOUNT_PASSWORD;
  if (!email || !password) throw new Error("Missing isolated credentials");
  await page.goto("/account");
  await page.getByLabel("电子邮箱").fill(email);
  await page.getByLabel("密码（12–256 字符）").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator('.account-module-nav a[href="#files"]').click();
  await expect(page.getByRole("heading", { name: "空间文件" })).toBeVisible();
  await page.getByLabel("选择文件").setInputFiles({ name: "browser-test.txt", mimeType: "text/plain", buffer: Buffer.from("real-private-file") });
  await page.getByRole("button", { name: "上传文件", exact: true }).click();
  const downloadButton = page.getByRole("button", { name: "下载 browser-test.txt", exact: true });
  await expect(downloadButton).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await downloadButton.click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("browser-test.txt");
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString()).toBe("real-private-file");
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "删除 browser-test.txt", exact: true }).click();
  await expect(downloadButton).toHaveCount(0);
});
