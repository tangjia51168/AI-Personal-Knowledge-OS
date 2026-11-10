import { expect, test } from "@playwright/test";

test.use({ trace: "off", screenshot: "off", video: "off" });

test("learning proxy keeps exact paths and methods closed for anonymous callers", async ({ page }) => {
  const root = "/api/account/spaces/00000000-0000-0000-0000-000000000001";
  const note = `${root}/notes/00000000-0000-0000-0000-000000000002/learning`;
  const review = note.replace(/learning$/, "review");
  expect((await page.request.put(review, {data:{status:"seen",reminder_at:null,expected:"a".repeat(64)}})).status()).toBe(401);
  for (const method of ["get","post","delete"] as const) expect((await page.request[method](review)).status()).toBe(405);
  expect((await page.request.put(review + "?user_id=forged", {data:{}})).status()).toBe(422);
  expect((await page.request.put(review, {data:{expected:"x".repeat(5000)}})).status()).toBe(413);
  expect((await page.request.put(review + "/extra", {data:{}})).status()).toBe(404);
  const batch = `${root}/learning/reminders/batch`, preview = `${root}/learning/reminders/preview`;
  const plan = `${root}/learning/reminders/plan`;
  expect((await page.request.put(plan, { data: {items:[{note_id:"00000000-0000-0000-0000-000000000002",expected:"a".repeat(64),reminder_at:"2097-01-01T12:00:00Z"}]} })).status()).toBe(401);
  for (const method of ["get", "post", "delete"] as const) expect((await page.request[method](plan)).status()).toBe(405);
  expect((await page.request.put(plan + "?user_id=forged", {data:{}})).status()).toBe(422);
  expect((await page.request.put(plan, {data:{items:"x".repeat(5000)}})).status()).toBe(413);
  expect((await page.request.put(plan + "/extra", {data:{}})).status()).toBe(404);
  expect((await page.request.put(batch, { data: { items: [{note_id:"00000000-0000-0000-0000-000000000002",expected:"a".repeat(64)}],reminder_at:null } })).status()).toBe(401);
  expect((await page.request.post(preview, { data: {note_ids:["00000000-0000-0000-0000-000000000002"]} })).status()).toBe(401);
  expect((await page.request.get(batch)).status()).toBe(405);
  expect((await page.request.put(preview)).status()).toBe(405);
  expect((await page.request.post(preview + "?user_id=forged", {data:{}})).status()).toBe(422);
  expect((await page.request.put(batch, {data:{items:"x".repeat(5000)}})).status()).toBe(413);
  expect((await page.request.post(preview + "/extra", {data:{}})).status()).toBe(404);
  expect((await page.request.get(`${root}/learning`)).status()).toBe(401);
  for (const endpoint of ["schedule", "calendar"]) {
    const path = `${root}/learning/${endpoint}`;
    expect((await page.request.get(path + "?period=7")).status()).toBe(401);
    for (const query of ["period=365", "period=7&period=30", "user_id=forged", "period="]) expect((await page.request.get(path + "?" + query)).status()).toBe(422);
    for (const method of ["post", "put", "delete"] as const) expect((await page.request[method](path)).status()).toBe(405);
    expect((await page.request.get(path + "/extra")).status()).toBe(404);
  }
  expect((await page.request.get(`${root}/notes?reading_status=unseen&review=due`)).status()).toBe(401);
  for (const query of ["reading_status=invalid", "review=invalid", "reading_status=seen&reading_status=unseen", "review=due&review=upcoming", "user_id=forged"]) {
    expect((await page.request.get(`${root}/notes?${query}`)).status()).toBe(422);
  }
  expect((await page.request.get(`${root}/learning?due=true&due=false`)).status()).toBe(422);
  expect((await page.request.post(`${root}/learning`, { data: {} })).status()).toBe(405);
  const summary = `${root}/learning/summary`;
  expect((await page.request.get(summary)).status()).toBe(401);
  expect((await page.request.get(summary + "?user_id=forged")).status()).toBe(422);
  expect((await page.request.get(summary + "?due=true&due=false")).status()).toBe(422);
  for (const method of ["post", "put", "delete"] as const) expect((await page.request[method](summary)).status()).toBe(405);
  expect((await page.request.get(summary + "/extra")).status()).toBe(404);
  const analysis = `${root}/learning/analysis`;
  expect((await page.request.get(analysis)).status()).toBe(401);
  expect((await page.request.get(analysis + "?user_id=forged")).status()).toBe(422);
  for (const method of ["post", "put", "delete"] as const) expect((await page.request[method](analysis)).status()).toBe(405);
  expect((await page.request.get(analysis + "/extra")).status()).toBe(404);
  expect((await page.request.get(note)).status()).toBe(405);
  expect((await page.request.put(`${note}/extra`, { data: {} })).status()).toBe(404);
  expect((await page.request.put(note, { data: { status: "x".repeat(20000) } })).status()).toBe(413);
});
