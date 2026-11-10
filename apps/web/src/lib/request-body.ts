export async function boundedBody(request: Request, limit: number, timeoutMs = 30000): Promise<ArrayBuffer | undefined> {
  if (request.method === "GET" || !request.body) return undefined;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  const deadline = Date.now() + timeoutMs;
  try {
    while (true) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const chunk = await Promise.race([
        reader.read(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            // Reject first: cancel resolves a pending read as done=true.
            reject(new Error("BODY_TIMEOUT"));
            void reader.cancel().catch(() => {});
          }, Math.max(0, deadline - Date.now()));
        }),
      ]).finally(() => { if (timer) clearTimeout(timer); });
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > limit) { await reader.cancel(); throw new Error("TOO_LARGE"); }
      chunks.push(chunk.value);
    }
  } finally { reader.releaseLock(); }
  const merged = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.byteLength; }
  return merged.buffer;
}
