import { NextRequest, NextResponse } from "next/server";
import { boundedBody } from "@/lib/request-body";
import { browserMutationAllowed, sessionCookie, sessionDeadline, validSessionToken } from "@/lib/account-session";

const allowed = /^(auth\/(register|login|logout|logout-all|me)|spaces(?:\/[0-9a-f-]{36}(?:\/members(?:\/[0-9a-f-]{36})?|\/jobs(?:\/[0-9a-f-]{36}(?:\/(result|cancel|retry))?)?|\/assets(?:\/[0-9a-f-]{36}(?:\/(download-ticket|download))?)?)?)?)$/;
const modelPaths = /^spaces\/[0-9a-f-]{36}\/(settings|model-options|ai-usage|ai-test|providers(?:\/[0-9a-f-]{36}\/(disable|rotate-encryption|update))?)$/;
const auditPath = /^spaces\/[0-9a-f-]{36}\/audit-events$/;
const sourceInspectPath = /^spaces\/[0-9a-f-]{36}\/sources\/inspect$/;
const sourcePath = /^spaces\/[0-9a-f-]{36}\/sources$/;
const collectionPath = /^spaces\/[0-9a-f-]{36}\/collections(?:\/[0-9a-f-]{36}(?:\/items(?:\/[0-9a-f-]{36})?)?)?$/;
const sourceSchedulePath = /^spaces\/[0-9a-f-]{36}\/sources\/schedules(?:\/[0-9a-f-]{36})?$/;
const sourceReadPath = /^spaces\/[0-9a-f-]{36}\/sources\/(?:capabilities|[0-9a-f-]{36}\/snapshots(?:\/[0-9a-f-]{36})?)$/;
const collectPath = /^spaces\/[0-9a-f-]{36}\/sources\/[0-9a-f-]{36}\/collect$/;
const sourceImportPath = /^spaces\/[0-9a-f-]{36}\/sources\/[0-9a-f-]{36}\/imports$/;
const summarizePath = /^spaces\/[0-9a-f-]{36}\/snapshots\/[0-9a-f-]{36}\/summarize$/;
const summaryResumePath = /^spaces\/[0-9a-f-]{36}\/note-runs\/[0-9a-f-]{36}\/resume$/;
const snapshotRunsPath = /^spaces\/[0-9a-f-]{36}\/snapshots\/[0-9a-f-]{36}\/note-runs$/;
const firstNotePath = /^spaces\/[0-9a-f-]{36}\/snapshots\/[0-9a-f-]{36}\/notes$/;
const firstNoteEvidencePath = /^spaces\/[0-9a-f-]{36}\/snapshots\/[0-9a-f-]{36}\/note-evidence$/;
const noteReadPath = /^spaces\/[0-9a-f-]{36}\/(?:note-runs\/[0-9a-f-]{36}|notes(?:\/[0-9a-f-]{36}(?:\/revisions\/[0-9a-f-]{36})?)?)$/;
const noteListPath = /^spaces\/[0-9a-f-]{36}\/notes$/;
const knowledgePath = /^spaces\/[0-9a-f-]{36}\/knowledge\/(search|graph|research)$/;
const semanticPath = /^spaces\/[0-9a-f-]{36}\/knowledge\/semantic\/(index|search)$/;
const answerPath = /^spaces\/[0-9a-f-]{36}\/knowledge\/answers(?:\/[0-9a-f-]{36})?$/;
const noteExportPath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/revisions\/[0-9a-f-]{36}\/export$/;
const noteBatchExportPath = /^spaces\/[0-9a-f-]{36}\/notes\/export-batch$/;
const noteEditPath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/revisions$/;
const annotationPath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/revisions\/[0-9a-f-]{36}\/annotations$/;
const conceptPath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/revisions\/[0-9a-f-]{36}\/concepts$/;
const noteLinkReadPath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/links$/;
const noteGraphPath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/graph$/;
const noteLinkWritePath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/links\/[0-9a-f-]{36}$/;
const learningPath = /^spaces\/[0-9a-f-]{36}\/learning$/;
const notificationsPath = /^spaces\/[0-9a-f-]{36}\/learning\/notifications(?:\/(?:preference|test|[0-9a-f-]{36}\/read))?$/;
const learningSummaryPath = /^spaces\/[0-9a-f-]{36}\/learning\/summary$/;
const learningAnalysisPath = /^spaces\/[0-9a-f-]{36}\/learning\/analysis$/;
const learningSchedulePath = /^spaces\/[0-9a-f-]{36}\/learning\/(schedule|calendar)$/;
const reminderBatchPath = /^spaces\/[0-9a-f-]{36}\/learning\/reminders\/(preview|batch|plan)$/;
const learningWritePath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/(learning|review)$/;
const labelPath = /^spaces\/[0-9a-f-]{36}\/labels$/;
const labelManagePath = /^spaces\/[0-9a-f-]{36}\/labels\/manage$/;
const labelUpdatePath = /^spaces\/[0-9a-f-]{36}\/labels\/[0-9a-f-]{36}$/;
const noteLabelReadPath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/labels$/;
const noteLabelWritePath = /^spaces\/[0-9a-f-]{36}\/notes\/[0-9a-f-]{36}\/labels\/[0-9a-f-]{36}$/;
const recoveryPath = /^auth\/(request-verification|request-password-reset|verify-email|reset-password)$/;
const securityEventsPath = "auth/security-events";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const relative = path.join("/");
  if (collectionPath.test(relative)) {
    const methods = relative.endsWith("/collections") ? ["GET", "POST"] : relative.endsWith("/items") ? ["GET"] : /\/items\//.test(relative) ? ["PUT"] : ["PATCH"];
    if (!methods.includes(request.method)) return NextResponse.json({error:{code:"METHOD_NOT_ALLOWED"}}, {status:405, headers:{Allow:methods.join(", ")}});
    if (request.nextUrl.search) return NextResponse.json({error:{code:"INVALID_COLLECTION_QUERY"}}, {status:422});
  }
  if (notificationsPath.test(relative)) {
    const method = relative.endsWith("/notifications") ? "GET" : relative.endsWith("/preference") ? "PUT" : "POST";
    if (request.method !== method) return NextResponse.json({error:{code:"METHOD_NOT_ALLOWED"}}, {status:405, headers:{Allow:method}});
    if (request.nextUrl.search) return NextResponse.json({error:{code:"INVALID_NOTIFICATION_QUERY"}}, {status:422});
  }
  if (semanticPath.test(relative)) {
    const method = relative.endsWith("/index") ? "POST" : "GET";
    if (request.method !== method) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: method } });
    if ([...request.nextUrl.searchParams.keys()].some(key => method === "POST" || !["q", "limit"].includes(key)) || [...request.nextUrl.searchParams.keys()].some(key => request.nextUrl.searchParams.getAll(key).length > 1)) return NextResponse.json({ error: { code: "INVALID_SEMANTIC_QUERY" } }, { status: 422 });
  }
  const cookie = request.cookies.get(sessionCookie)?.value;
  const isSignIn = relative === "auth/login" || relative === "auth/register";
  if (!["GET", "HEAD"].includes(request.method) && (cookie || isSignIn) && !browserMutationAllowed(request)) {
    return NextResponse.json({ error: { code: "CROSS_ORIGIN_REQUEST_REJECTED" } }, { status: 403, headers: { "Cache-Control": "no-store" } });
  }
  const secureCookie = request.nextUrl.protocol === "https:" || request.headers.get("x-forwarded-proto") === "https";
  const cookieOptions = { httpOnly: true, secure: secureCookie, sameSite: "strict" as const, path: "/api/account" };
  if (sourceSchedulePath.test(relative)) {
    const methods = relative.endsWith("/schedules") ? ["GET", "POST"] : ["PATCH"];
    if (!methods.includes(request.method)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: methods.join(", ") } });
    if (request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_SCHEDULE_QUERY" } }, { status: 422 });
  }
  if (answerPath.test(relative)) {
    const methods = relative.endsWith("/answers") ? ["GET", "POST"] : ["GET"];
    if (!methods.includes(request.method)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: methods.join(", ") } });
    if (request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_RESEARCH_QUERY" } }, { status: 422 });
  }
  if (firstNotePath.test(relative)) {
    if (request.method !== "POST") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "POST" } });
    if (request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_NOTE_CREATE_QUERY" } }, { status: 422 });
  }
  if (firstNoteEvidencePath.test(relative)) {
    if (request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
    if (request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_NOTE_CREATE_QUERY" } }, { status: 422 });
  }
  // Concepts share only the exact annotation-shaped endpoint; queries have no semantics.
  if (conceptPath.test(relative)) {
    if (!["GET", "POST"].includes(request.method)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET, POST" } });
    if (request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_CONCEPT_QUERY" } }, { status: 422 });
  }
  if (knowledgePath.test(relative)) {
    if (request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
    const query = request.nextUrl.searchParams;
    const names = relative.endsWith("/search") || relative.endsWith("/research") ? ["q", "scope", "platform", "origin", "label_id", "cursor", "limit"] : [];
    if (Array.from(query.keys()).some(name => !names.includes(name) || query.getAll(name).length !== 1)) return NextResponse.json({ error: { code: "INVALID_KNOWLEDGE_QUERY" } }, { status: 422 });
  }
  if ((labelManagePath.test(relative) || labelUpdatePath.test(relative)) && request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_LABEL_QUERY" } }, { status: 422 });
  if (learningWritePath.test(relative) && relative.endsWith("/review") && request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_REVIEW_QUERY" } }, { status: 422 });
  if (reminderBatchPath.test(relative)) {
    const method = relative.endsWith("/preview") ? "POST" : "PUT";
    if (request.method !== method) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: method } });
    if (request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_REMINDER_QUERY" } }, { status: 422 });
  }
  if (learningSchedulePath.test(relative)) {
    if (request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
    const query = request.nextUrl.searchParams;
    if (Array.from(query.keys()).some(name => name !== "period" || query.getAll(name).length !== 1)
      || (query.has("period") && !["due", "7", "30", "90"].includes(query.get("period")!))) return NextResponse.json({ error: { code: "INVALID_SCHEDULE_QUERY" } }, { status: 422 });
  }
  if (noteListPath.test(relative) && request.method === "GET") {
    const names = ["q", "platform", "origin", "label_id", "reading_status", "review", "cursor", "limit"];
    const query = request.nextUrl.searchParams;
    if (Array.from(query.keys()).some(name => !names.includes(name) || query.getAll(name).length !== 1)
      || (query.has("reading_status") && !["unseen", "seen", "skimmed", "deep_read", "unrecorded"].includes(query.get("reading_status")!))
      || (query.has("review") && !["due", "upcoming"].includes(query.get("review")!))) return NextResponse.json({ error: { code: "INVALID_NOTE_FILTER" } }, { status: 422 });
  }
  if (learningSummaryPath.test(relative)) {
    if (request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
    if (request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_LEARNING_QUERY" } }, { status: 422 });
  }
  if (learningAnalysisPath.test(relative)) {
    if (request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
    if (request.nextUrl.search) return NextResponse.json({ error: { code: "INVALID_LEARNING_ANALYSIS_QUERY" } }, { status: 422 });
  }
  if (noteGraphPath.test(relative)) {
    if (request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
    const depths = request.nextUrl.searchParams.getAll("depth");
    if (depths.length > 1 || (depths.length === 1 && !["1", "2"].includes(depths[0]))) return NextResponse.json({ error: { code: "INVALID_GRAPH_DEPTH" } }, { status: 422 });
  }
  if (summaryResumePath.test(relative) && (request.method !== "POST" || request.nextUrl.search)) return NextResponse.json({error:{code:"INVALID_SUMMARY_RESUME"}}, {status:request.method !== "POST" ? 405 : 422});
  if (snapshotRunsPath.test(relative) && (request.method !== "GET" || request.nextUrl.search)) return NextResponse.json({error:{code:"INVALID_SUMMARY_QUERY"}}, {status:request.method !== "GET" ? 405 : 422});
  const paths = [semanticPath, allowed, modelPaths, auditPath, sourceInspectPath, sourcePath, sourceSchedulePath, sourceReadPath, collectPath, sourceImportPath, summarizePath, summaryResumePath, firstNotePath, firstNoteEvidencePath, knowledgePath, answerPath, noteReadPath, noteExportPath, noteBatchExportPath, noteEditPath, annotationPath, conceptPath, noteLinkReadPath, noteLinkWritePath, noteGraphPath, labelPath, labelManagePath, labelUpdatePath, noteLabelReadPath, noteLabelWritePath, learningPath, learningSummaryPath, learningAnalysisPath, learningSchedulePath, reminderBatchPath, learningWritePath, recoveryPath];
  if (!paths.some(pattern => pattern.test(relative)) && !collectionPath.test(relative) && !notificationsPath.test(relative) && !snapshotRunsPath.test(relative) && relative !== securityEventsPath) return NextResponse.json({ error: { code: "NOT_FOUND" } }, { status: 404 });
  if (noteExportPath.test(relative) && request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
  if (noteExportPath.test(relative) && (request.nextUrl.searchParams.getAll("format").length > 1 || (request.nextUrl.searchParams.has("format") && !["markdown", "html", "json", "text", "obsidian", "pptx", "image", "flowchart"].includes(request.nextUrl.searchParams.get("format")!)))) return NextResponse.json({ error: { code: "INVALID_EXPORT_FORMAT" } }, { status: 422 });
  if (request.method === "PUT" && !collectionPath.test(relative) && !notificationsPath.test(relative) && !noteLabelWritePath.test(relative) && !noteLinkWritePath.test(relative) && !learningWritePath.test(relative) && !reminderBatchPath.test(relative)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405 });
  if (request.method === "PATCH" && !collectionPath.test(relative) && !labelUpdatePath.test(relative) && !sourceSchedulePath.test(relative)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405 });
  if (noteBatchExportPath.test(relative) && request.method !== "POST") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "POST" } });
  if (learningPath.test(relative) && (request.nextUrl.searchParams.getAll("due").length > 1 || (request.nextUrl.searchParams.has("due") && !["true", "false"].includes(request.nextUrl.searchParams.get("due")!)) || request.nextUrl.searchParams.getAll("note_id").length > 1 || (request.nextUrl.searchParams.has("note_id") && !/^[0-9a-f-]{36}$/.test(request.nextUrl.searchParams.get("note_id")!)))) return NextResponse.json({ error: { code: "INVALID_LEARNING_QUERY" } }, { status: 422 });
  if (annotationPath.test(relative) && !["GET", "POST"].includes(request.method)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET, POST" } });
  if (labelPath.test(relative) && !["GET", "POST"].includes(request.method)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET, POST" } });
  if (labelManagePath.test(relative) && request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
  if (labelUpdatePath.test(relative) && request.method !== "PATCH") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "PATCH" } });
  if (noteLabelReadPath.test(relative) && request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
  if (noteLabelWritePath.test(relative) && !["PUT", "DELETE"].includes(request.method)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "PUT, DELETE" } });
  if (noteLinkReadPath.test(relative) && request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
  if (noteLinkWritePath.test(relative) && !["PUT", "DELETE"].includes(request.method)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "PUT, DELETE" } });
  if (learningPath.test(relative) && request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
  if (learningWritePath.test(relative) && request.method !== "PUT") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "PUT" } });
  if (sourceInspectPath.test(relative) && request.method !== "POST") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "POST" } });
  if (sourcePath.test(relative) && !["GET", "POST"].includes(request.method)) return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET, POST" } });
  if ((sourceReadPath.test(relative) || noteReadPath.test(relative)) && request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
  if ((collectPath.test(relative) || sourceImportPath.test(relative) || summarizePath.test(relative) || firstNotePath.test(relative) || noteEditPath.test(relative)) && request.method !== "POST") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "POST" } });
  if (relative === securityEventsPath && request.method !== "GET") return NextResponse.json({ error: { code: "METHOD_NOT_ALLOWED" } }, { status: 405, headers: { Allow: "GET" } });
  const isUpload = /^spaces\/[0-9a-f-]{36}\/assets$/.test(relative) && request.method === "POST";
  const isCalendar = learningSchedulePath.test(relative) && relative.endsWith("/calendar");
  const isDownload = ((relative.endsWith("/download") || noteExportPath.test(relative) || isCalendar) && request.method === "GET") || (noteBatchExportPath.test(relative) && request.method === "POST");
  const headers: Record<string, string> = { "Content-Type": isUpload ? (request.headers.get("content-type") ?? "application/octet-stream") : "application/json" };
  const auth = request.headers.get("authorization");
  if (validSessionToken(cookie) && !isSignIn) headers.Authorization = `Bearer ${cookie}`;
  else if (auth && !isSignIn) headers.Authorization = auth;
  let body: ArrayBuffer | undefined;
  try { body = await boundedBody(request, learningWritePath.test(relative) && relative.endsWith("/review") ? 4096 : isUpload ? 10 * 1024 * 1024 : noteBatchExportPath.test(relative) ? 16 * 1024 : firstNotePath.test(relative) || sourceImportPath.test(relative) || noteEditPath.test(relative) ? 128 * 1024 : sourceInspectPath.test(relative) || sourcePath.test(relative) ? 17 * 1024 : answerPath.test(relative) || conceptPath.test(relative) || modelPaths.test(relative) || annotationPath.test(relative) || learningWritePath.test(relative) || labelUpdatePath.test(relative) ? 16384 : 4096); }
  catch (error) {
    const timeout = error instanceof Error && error.message === "BODY_TIMEOUT";
    return NextResponse.json({ error: { code: timeout ? "BODY_TIMEOUT" : "INVALID_OR_LARGE_BODY" } }, { status: timeout ? 408 : 413 });
  }
  const query = new URLSearchParams();
  const listQuery = request.method === "GET" ? semanticPath.test(relative) ? ["q", "limit"] : knowledgePath.test(relative) ? ["q", "scope", "platform", "origin", "label_id", "cursor", "limit"] : sourcePath.test(relative) ? ["q", "platform", "status", "cursor", "limit"] : noteListPath.test(relative) ? ["q", "platform", "origin", "label_id", "reading_status", "review", "cursor", "limit"] : annotationPath.test(relative) ? ["cursor", "limit"] : learningPath.test(relative) ? ["due", "note_id"] : [] : [];
  for (const name of (isUpload ? ["filename"] : learningSchedulePath.test(relative) ? ["period"] : noteGraphPath.test(relative) ? ["depth"] : noteExportPath.test(relative) ? ["format"] : isDownload ? ["expires", "signature"] : auditPath.test(relative) || relative === securityEventsPath ? ["cursor", "limit"] : listQuery)) {
    const value = request.nextUrl.searchParams.get(name);
    if (value !== null) query.set(name, value);
  }
  try {
    const upstream = await fetch(`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/api/v1/${relative}?${query}`, {
      method: request.method, headers, body: body || undefined, cache: "no-store",
      signal: AbortSignal.timeout(semanticPath.test(relative) ? 240000 : summarizePath.test(relative) || summaryResumePath.test(relative) ? 570000 : answerPath.test(relative) ? 135000 : relative.endsWith("/ai-test") ? 65000 : 15000), redirect: "error",
    });
    const responseHeaders: Record<string, string> = { "Content-Type": upstream.headers.get("content-type") ?? "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
    if (isDownload && upstream.ok) responseHeaders["Content-Disposition"] = upstream.headers.get("content-disposition") ?? "attachment";
  if (noteExportPath.test(relative) || noteBatchExportPath.test(relative) || isCalendar) responseHeaders["Content-Security-Policy"] = "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'";
    if (isSignIn && upstream.ok) {
      const result = await upstream.json();
      if (result.data?.access_token) {
        const expiry = sessionDeadline(result.data.expires_at);
        if (!validSessionToken(result.data.access_token) || !expiry) throw new Error("INVALID_SESSION_RESPONSE");
        const token = result.data.access_token;
        delete result.data.access_token;
        delete result.data.token_type;
        const response = NextResponse.json(result, { status: upstream.status, headers: responseHeaders });
        response.cookies.set(sessionCookie, token, { ...cookieOptions, expires: expiry });
        return response;
      }
      return NextResponse.json(result, { status: upstream.status, headers: responseHeaders });
    }
    const response = new NextResponse(upstream.body, {
      status: upstream.status, headers: responseHeaders,
    });
    if (upstream.status === 401 || (upstream.ok && ["auth/logout", "auth/logout-all"].includes(relative))) {
      response.cookies.set(sessionCookie, "", { ...cookieOptions, maxAge: 0 });
    }
    return response;
  } catch {
    return NextResponse.json({ error: { code: "API_UNAVAILABLE" } }, { status: 503 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
