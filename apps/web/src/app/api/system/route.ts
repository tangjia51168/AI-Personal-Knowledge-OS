import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

type Probe = { status: "up" | "down" };

export async function GET() {
  // Operator-controlled upstream only, never a URL supplied by a request/query.
  const origin = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
  let api: Probe = { status: "down" };
  let dependencies: Record<string, Probe> | null = null;
  await Promise.all([
    (async () => {
      try {
        const res = await fetch(`${origin}/health/live`, {
          cache: "no-store", signal: AbortSignal.timeout(5000), redirect: "error",
        });
        const body = await res.json();
        if (res.ok && body.data?.status === "ok") api = { status: "up" };
      } catch { /* An unreachable API is a real, visible state. */ }
    })(),
    (async () => {
      try {
        const res = await fetch(`${origin}/health/ready`, {
          cache: "no-store", signal: AbortSignal.timeout(12000), redirect: "error",
        });
        const body = await res.json();
        if (body.data?.services) {
          dependencies = Object.fromEntries(["postgresql", "redis", "storage"].map((name) => [
            name, { status: body.data.services[name]?.status === "up" ? "up" : "down" },
          ]));
        }
      } catch { /* Do not expose upstream errors, URLs or secrets. */ }
    })(),
  ]);
  return NextResponse.json({
    data: { api, dependencies, checkedAt: new Date().toISOString() },
  }, { headers: { "Cache-Control": "no-store" } });
}
