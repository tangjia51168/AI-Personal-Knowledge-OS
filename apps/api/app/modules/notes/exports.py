"""Bounded, inert single-revision exports; no storage or external I/O."""

import hashlib
import html
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.modules.identity.access import SpaceAccess
from app.modules.notes.content import SECTION_LABELS
from app.modules.notes.models import EvidenceItem, Note, NoteRevision
from app.modules.notes.obsidian import render_obsidian
from app.modules.notes.visual_exports import render_flowchart_svg, render_image_svg, render_pptx
from app.modules.sources.models import Source, SourceSnapshot

ExportFormat = Literal["markdown", "html", "json", "text", "obsidian", "pptx", "image", "flowchart"]
MAX_EXPORT_BYTES = 1_048_576
DISCLOSURE = (
    "单篇笔记的指定不可变修订；不含其他版本、标签、双向链接或逐段批注。"
    "引用有效不代表事实核验；个人理解未经来源核验。"
    "导出不重新访问原链接，不调用模型，也不是完整空间备份。"
)


def export_data(access: SpaceAccess, note_id: UUID, revision_id: UUID) -> dict[str, Any]:
    session, tenant = access.actor.session, access.tenant.id
    # Every join is tenant-bound as well as protected by the existing FORCE RLS.
    row = session.execute(
        select(Note, NoteRevision, Source, SourceSnapshot)
        .select_from(Note)
        .join(
            NoteRevision,
            (NoteRevision.tenant_id == Note.tenant_id) & (NoteRevision.note_id == Note.id),
        )
        .join(Source, (Source.tenant_id == Note.tenant_id) & (Source.id == Note.source_id))
        .join(
            SourceSnapshot,
            (SourceSnapshot.tenant_id == Note.tenant_id)
            & (SourceSnapshot.id == NoteRevision.snapshot_id)
            & (SourceSnapshot.source_id == Note.source_id),
        )
        .where(Note.tenant_id == tenant, Note.id == note_id, NoteRevision.id == revision_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "REVISION_NOT_FOUND"})
    note, revision, source, snapshot = row
    evidence = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.tenant_id == tenant,
                EvidenceItem.revision_id == revision_id,
                EvidenceItem.snapshot_id == snapshot.id,
            )
            .order_by(EvidenceItem.evidence_key)
            .limit(249)
        )
    )
    if len(evidence) > 248:
        raise HTTPException(413, detail={"code": "NOTE_EXPORT_LIMIT"})
    # Explicit fields: never serialize ORM internals, job payloads, credentials or run claims.
    return {
        "schema": "knowledge-note-export-v1",
        "scope": DISCLOSURE,
        "note_id": str(note.id),
        "revision": {
            "id": str(revision.id),
            "version": revision.version,
            "title": revision.title,
            "origin": revision.origin,
            "created_by": str(revision.created_by),
            "created_at": revision.created_at.isoformat(),
            "base_revision_id": str(revision.base_revision_id)
            if revision.base_revision_id
            else None,
            "document": revision.document,
            "content_md": revision.content_md,
            "content_md_sha256": hashlib.sha256(revision.content_md.encode("utf-8")).hexdigest(),
        },
        "source": {
            "id": str(source.id),
            "original_url": source.original_url,
            "canonical_url": source.canonical_url,
        },
        "snapshot": {
            "id": str(snapshot.id),
            "version": snapshot.version,
            "provenance": snapshot.provenance,
            "platform": snapshot.platform,
            "content_kind": snapshot.content_kind,
            "title": snapshot.title,
            "author": snapshot.author,
            "created_at": snapshot.created_at.isoformat(),
            "raw_sha256": snapshot.raw_sha256,
            "limitations": snapshot.limitations,
        },
        "evidence": [{"key": item.evidence_key, "quote": item.quote} for item in evidence],
    }


def export_batch_data(access: SpaceAccess, items: list[tuple[UUID, UUID]]) -> list[dict[str, Any]]:
    if not items or len(items) > 20 or len(set(items)) != len(items):
        raise HTTPException(422, detail={"code": "NOTE_BATCH_LIMIT"})
    return [export_data(access, note_id, revision_id) for note_id, revision_id in items]


def plain_text(data: dict[str, Any]) -> str:
    revision, source, snapshot = data["revision"], data["source"], data["snapshot"]
    document = revision["document"]
    lines = [
        revision["title"],
        DISCLOSURE,
        f"笔记：{data['note_id']} / 修订：{revision['id']} / 版本：{revision['version']}",
        f"版本来源：{revision['origin']} / 作者：{revision['created_by']} / "
        f"时间：{revision['created_at']}",
        f"基准修订：{revision['base_revision_id'] or '无'}",
        f"原链接：{source['original_url']}",
        f"规范链接：{source['canonical_url']}",
        f"快照：{snapshot['id']} / 版本：{snapshot['version']} / 来源：{snapshot['provenance']}",
        f"平台：{snapshot['platform']} / 内容类型：{snapshot['content_kind']}",
        f"来源标题：{snapshot['title']} / 来源作者：{snapshot['author']}",
        f"快照时间：{snapshot['created_at']} / 原响应摘要：{snapshot['raw_sha256']}",
        "来源限制：" + json.dumps(snapshot["limitations"], ensure_ascii=False),
        "",
        "概述",
    ]

    def item_text(item: dict[str, Any]) -> str:
        return item["text"] + " " + " ".join(f"[{key}]" for key in item["evidence_keys"])

    lines.append(item_text(document["overview"]))
    present = {section["kind"] for section in document["sections"]}
    for section in document["sections"]:
        lines.extend(["", SECTION_LABELS[section["kind"]]])
        lines.extend(item_text(item) for item in section["items"])
    lines.extend(["", "缺失信息与限制"])
    lines.extend(document["limitations"])
    lines.append(
        "未生成类别："
        + "、".join(label for kind, label in SECTION_LABELS.items() if kind not in present)
    )
    lines.append(
        "证据范围（不是事实核验或语义覆盖率评分）："
        + json.dumps(document["coverage"], ensure_ascii=False, sort_keys=True)
    )
    if revision["origin"] == "user_edit":
        lines.extend(
            [
                "",
                "人工修订与个人理解（未经来源核验）",
                document["edit_disclaimer"],
                document["personal_notes"] or "本版本未追加个人理解。",
            ]
        )
    lines.extend(["", "来源证据片段"])
    for item in data["evidence"]:
        lines.extend([item["key"], item["quote"], ""])
    return "\n".join(lines).rstrip() + "\n"


def render_export(data: dict[str, Any], format: ExportFormat) -> tuple[bytes, str, str]:
    if format == "obsidian":
        return render_obsidian(data), "application/zip", "zip"
    if format == "image":
        return render_image_svg(data), "image/svg+xml", "svg"
    if format == "flowchart":
        return render_flowchart_svg(data), "image/svg+xml", "svg"
    if format == "pptx":
        return (
            render_pptx(data),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "pptx",
        )
    if format == "json":
        content = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        media, suffix = "application/json", "json"
    elif format == "markdown":
        # Preserve the exact immutable Markdown, including its original disclosures.
        content, media, suffix = data["revision"]["content_md"], "text/markdown", "md"
    elif format == "text":
        content, media, suffix = plain_text(data), "text/plain", "txt"
    else:
        # Inert standalone document: no Markdown/HTML interpreter, external resources or links.
        content = (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "base-uri 'none'; form-action 'none'\">"
            f"<title>{html.escape(data['revision']['title'])}</title></head><body><pre>"
            + html.escape(plain_text(data))
            + "</pre></body></html>\n"
        )
        media, suffix = "text/html", "html"
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_EXPORT_BYTES:
        raise HTTPException(413, detail={"code": "NOTE_EXPORT_LIMIT"})
    return encoded, media, suffix
