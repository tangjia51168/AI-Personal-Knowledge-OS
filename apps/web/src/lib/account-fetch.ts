"use client";

// Components keep their existing request contracts; credentials stay HttpOnly.
export async function accountFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  headers.delete("Authorization");
  headers.set("X-Knowledge-Request", "1");
  const response = await fetch(input, { ...init, headers, credentials: "same-origin" });
  if (response.status === 401) window.dispatchEvent(new Event("knowledge-session-expired"));
  return response;
}
