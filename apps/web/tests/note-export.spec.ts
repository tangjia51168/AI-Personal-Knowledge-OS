import { expect, test } from "@playwright/test";

test("note export proxy restricts exact revision paths, methods and formats", async ({ request }) => {
  const root = "/api/account/spaces/00000000-0000-0000-0000-000000000001/notes/00000000-0000-0000-0000-000000000002/revisions/00000000-0000-0000-0000-000000000003/export";
  for (const format of ["markdown", "html", "json", "text", "obsidian", "pptx", "image", "flowchart"]) {
    expect((await request.get(`${root}?format=${format}`)).status()).toBe(401);
  }
  expect((await request.get(`${root}?format=pdf`)).status()).toBe(422);
  expect((await request.get(`${root}?format=`)).status()).toBe(422);
  expect((await request.get(`${root}?format=html&format=json`)).status()).toBe(422);
  expect((await request.get(`${root}/extra`)).status()).toBe(404);
  expect((await request.post(root)).status()).toBe(405);
  expect((await request.delete(root)).status()).toBe(405);
  expect((await request.put(root)).status()).toBe(405);
  const batch = "/api/account/spaces/00000000-0000-0000-0000-000000000001/notes/export-batch";
  expect((await request.post(batch, { data: { items: [] } })).status()).toBe(401);
  for (const response of [await request.get(batch), await request.put(batch), await request.delete(batch)]) expect(response.status()).toBe(405);
  expect((await request.post(batch + "/extra", { data: {} })).status()).toBe(404);
});
