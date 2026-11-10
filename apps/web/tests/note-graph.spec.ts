import { expect, test } from "@playwright/test";

test("local graph proxy accepts only exact GET and bounded unambiguous depth", async ({ page }) => {
  const graph = "/api/account/spaces/00000000-0000-0000-0000-000000000001/notes/00000000-0000-0000-0000-000000000002/graph";
  for (const query of ["", "?depth=1", "?depth=2"]) expect((await page.request.get(graph + query)).status()).toBe(401);
  for (const query of ["?depth=0", "?depth=3", "?depth=", "?depth=01", "?depth=1&depth=2", "?depth=1&depth=1"]) expect((await page.request.get(graph + query)).status()).toBe(422);
  for (const method of ["POST", "PUT", "DELETE"]) expect((await page.request.fetch(graph, { method })).status()).toBe(405);
  expect((await page.request.get(graph + "/extra")).status()).toBe(404);
});
