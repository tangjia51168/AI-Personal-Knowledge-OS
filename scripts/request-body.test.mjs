import assert from "node:assert/strict";
import test from "node:test";
import { boundedBody } from "../apps/web/src/lib/request-body.ts";

test("slow body times out before cancellation can masquerade as successful EOF", async () => {
  let cancelled = false;
  const stream = new ReadableStream({ cancel() { cancelled = true; } });
  const request = new Request("http://localhost/test", { method: "POST", body: stream, duplex: "half" });
  await assert.rejects(boundedBody(request, 100, 10), /BODY_TIMEOUT/);
  assert.equal(cancelled, true);
});

test("stream exceeding byte cap is rejected without trusting Content-Length", async () => {
  const request = new Request("http://localhost/test", { method: "POST", body: "more-than-limit" });
  await assert.rejects(boundedBody(request, 3), /TOO_LARGE/);
});

test("valid multi-chunk body is preserved", async () => {
  const stream = new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode("first"));
    controller.enqueue(new TextEncoder().encode("second"));
    controller.close();
  } });
  const request = new Request("http://localhost/test", { method: "POST", body: stream, duplex: "half" });
  assert.equal(new TextDecoder().decode(await boundedBody(request, 100)), "firstsecond");
});
