"""Deterministic, bounded Markdown bundle; never opens or configures a local vault."""

import hashlib
import io
import json
import zipfile
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.modules.notes.content import SECTION_LABELS, _plain_markdown

MAX_BUNDLE_BYTES = 1_048_576
MAX_BATCH_ITEMS = 20
MAX_BATCH_BYTES = 8 * 1_048_576
BUNDLE_SCOPE = (
    "仅指定笔记的一个已保存修订与所引用证据；不含其他版本、标签、双向链接或批注。"
    "不是完整备份或同步，不包含Obsidian配置/插件，不访问原链接或调用模型。"
    "引用仅定位原文，不证明观点正确；个人理解未经来源或事实核验。"
)


def render_obsidian(data: dict[str, Any]) -> bytes:
    revision = data["revision"]
    # Only validated UUIDs enter paths/properties/links. Titles never become filenames.
    note_id, revision_id = str(UUID(data["note_id"])), str(UUID(revision["id"]))
    folder = f"knowledge-{note_id}/revision-{revision_id}/"
    document = revision["document"]
    quotes = {entry["key"]: entry["quote"] for entry in data["evidence"]}
    keys = sorted(
        {
            key
            for item in [
                document["overview"],
                *(item for section in document["sections"] for item in section["items"]),
            ]
            for key in item["evidence_keys"]
        }
    )
    # Evidence keys originate in the validated DB schema, not arbitrary wiki-link targets.
    if any(
        len(key) != 4 or key[0] != "E" or not key[1:].isascii() or not key[1:].isdigit()
        for key in keys
    ) or any(key not in quotes for key in keys):
        raise HTTPException(409, detail={"code": "NOTE_EXPORT_EVIDENCE_INVALID"})

    lines = [
        "---",
        "knowledge_os_schema: knowledge-obsidian-v1",
        f"knowledge_os_note_id: {note_id}",
        f"knowledge_os_revision_id: {revision_id}",
        "---",
        "",
        "# " + _plain_markdown(revision["title"]),
        "",
        BUNDLE_SCOPE,
        "",
        "来源与快照元数据见同目录 source.json；original.md 保留原修订Markdown字节。",
        "",
        "## 概述",
        "",
    ]

    def item_line(item: dict[str, Any]) -> str:
        return (
            _plain_markdown(item["text"])
            + " "
            + " ".join(f"[[{folder}evidence#{key}|{key}]]" for key in item["evidence_keys"])
        )

    lines.append(item_line(document["overview"]))
    for section in document["sections"]:
        lines.extend(["", "## " + SECTION_LABELS[section["kind"]], ""])
        lines.extend("- " + item_line(item) for item in section["items"])
    lines.extend(["", "## 缺失信息与限制", ""])
    lines.extend("- " + _plain_markdown(value) for value in document["limitations"])
    missing = [
        label
        for kind, label in SECTION_LABELS.items()
        if kind not in {section["kind"] for section in document["sections"]}
    ]
    if missing:
        lines.append("- 未生成类别：" + "、".join(missing))
    lines.append(
        "- 证据范围（不是事实核验或语义覆盖率评分）："
        + _plain_markdown(json.dumps(document["coverage"], ensure_ascii=False, sort_keys=True))
    )
    if document["coverage"]["partial"]:
        lines.append("- 仅涵盖快照前缀，不能视为全文总结。")
    if revision["origin"] == "user_edit":
        lines.extend(
            [
                "",
                "## 个人理解（未经来源或事实核验）",
                "",
                _plain_markdown(document["edit_disclaimer"]),
                "",
                _plain_markdown(document["personal_notes"] or "本版本未追加个人理解。"),
            ]
        )
    evidence_lines = ["# 来源证据", "", BUNDLE_SCOPE, "", f"[[{folder}note#概述|返回笔记概述]]"]
    for key in keys:
        evidence_lines.extend(["", f"## {key}", ""])
        evidence_lines.extend("> " + _plain_markdown(line) for line in quotes[key].splitlines())
    source = {
        "schema": "knowledge-obsidian-source-v1",
        "scope": BUNDLE_SCOPE,
        "note_id": note_id,
        "revision_id": revision_id,
        "origin": revision["origin"],
        "version": revision["version"],
        "created_by": revision["created_by"],
        "created_at": revision["created_at"],
        "base_revision_id": revision["base_revision_id"],
        "source": data["source"],
        "snapshot": data["snapshot"],
    }
    files = {
        "README.md": (
            "# Obsidian 单版本导出包\n\n"
            + BUNDLE_SCOPE
            + "\n\n1. 解压到新的空目录并核对 manifest.json 的大小和SHA-256；不要覆盖已有文件。\n"
            "2. 将完整 knowledge-UUID/revision-UUID 目录复制到自行选择的库中，打开 note.md。\n"
            "3. 点击 E 编号跳转同目录 evidence.md 的对应章节；可返回笔记概述。\n"
            "4. source.json 保存原始/规范URL、快照与作者/限制；URL不会自动访问。\n"
            "5. original.md 是原修订Markdown，note.md 是新增库内证据链接的阅读副本。\n\n"
            "无需插件，不包含 .obsidian 设置，不安装或启动Obsidian，不连接本地库。\n"
            "引用使用从vault根目录起的完整UUID路径，避免批量导出时同名evidence.md冲突。\n"
            "请保留knowledge-UUID目录在vault根部；采用Obsidian标准wiki语法，无需插件。\n"
            "本项目不保证第三方版本/插件兼容。\n"
            "这是用户手动文件导入，不是第三方应用自动集成验收。\n"
        ).encode("utf-8"),
        "note.md": ("\n".join(lines) + "\n").encode("utf-8"),
        "evidence.md": ("\n".join(evidence_lines) + "\n").encode("utf-8"),
        "source.json": (
            json.dumps(source, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8"),
        "original.md": revision["content_md"].encode("utf-8"),
    }
    manifest = {
        "schema": "knowledge-obsidian-bundle-v1",
        "note_id": note_id,
        "revision_id": revision_id,
        "scope": BUNDLE_SCOPE,
        "files": [
            {"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in sorted(files.items())
        ],
    }
    files["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
        raise HTTPException(413, detail={"code": "NOTE_EXPORT_LIMIT"})
    output = io.BytesIO()
    # STORED avoids compression bombs and platform-dependent compressor bytes.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(folder + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    result = output.getvalue()
    if len(result) > MAX_BUNDLE_BYTES:
        raise HTTPException(413, detail={"code": "NOTE_EXPORT_LIMIT"})
    return result


def render_obsidian_batch(documents: list[dict[str, Any]]) -> bytes:
    """Package explicitly selected immutable revisions into one bounded ZIP."""
    if not documents or len(documents) > MAX_BATCH_ITEMS:
        raise HTTPException(422, detail={"code": "NOTE_BATCH_LIMIT"})
    pairs = [(data["note_id"], data["revision"]["id"]) for data in documents]
    if len(set(pairs)) != len(pairs):
        raise HTTPException(422, detail={"code": "NOTE_BATCH_DUPLICATE"})
    output = io.BytesIO()
    total = 0
    file_records = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for data in sorted(documents, key=lambda item: (item["note_id"], item["revision"]["id"])):
            bundle = render_obsidian(data)
            total += len(bundle)
            if total > MAX_BATCH_BYTES:
                raise HTTPException(413, detail={"code": "NOTE_BATCH_LIMIT"})
            with zipfile.ZipFile(io.BytesIO(bundle)) as nested:
                for entry in nested.infolist():
                    # render_obsidian already creates UUID-only paths; preserve them
                    # and reject anything unexpected before copying into the batch.
                    if entry.is_dir() or entry.filename.startswith("/") or ".." in entry.filename:
                        raise HTTPException(409, detail={"code": "NOTE_EXPORT_PATH_INVALID"})
                    info = zipfile.ZipInfo(entry.filename, date_time=(1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    content = nested.read(entry)
                    archive.writestr(info, content)
                    file_records.append(
                        {
                            "path": entry.filename,
                            "bytes": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    )
        manifest = {
            "schema": "knowledge-obsidian-batch-v1",
            "scope": "仅明确选择的固定修订；不是完整备份。",
            "items": sorted(pairs),
            "files": file_records,
        }
        archive.writestr(
            zipfile.ZipInfo("batch-manifest.json", date_time=(1980, 1, 1, 0, 0, 0)),
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        )
    result = output.getvalue()
    if len(result) > MAX_BATCH_BYTES:
        raise HTTPException(413, detail={"code": "NOTE_BATCH_LIMIT"})
    return result
