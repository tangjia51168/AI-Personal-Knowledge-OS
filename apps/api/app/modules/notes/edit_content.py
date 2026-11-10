"""Validate human changes and render their provenance without a model call."""

import hashlib
import json
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.notes.content import (
    Evidence,
    SummaryDocument,
    SummaryItem,
    SummarySection,
    _model_text,
    _plain_markdown,
    parse_summary,
    render_markdown,
)

MAX_EDIT_BYTES = 65_536
EDIT_DISCLOSURE = (
    "此版本由用户人工修订。引用编号仅定位原始快照片段，不证明人工观点正确；"
    "个人理解未作来源或事实核验。"
)


class NoteDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: UUID
    title: str = Field(min_length=1, max_length=200)
    overview: SummaryItem
    sections: list[SummarySection] = Field(min_length=1, max_length=5)
    personal_notes: str = Field(max_length=4_000)

    _title = field_validator("title")(_model_text)

    @field_validator("sections")
    @classmethod
    def unique_sections(cls, value: list[SummarySection]) -> list[SummarySection]:
        if len({section.kind for section in value}) != len(value):
            raise ValueError("NOTE_EDIT_INVALID")
        return value

    @field_validator("personal_notes")
    @classmethod
    def plain_personal_notes(cls, value: str) -> str:
        # Personal understanding is optional, but follows the same safe text
        # rules as editable source-bound statements. It never becomes HTML.
        return _model_text(value) if value.strip() else ""

    @model_validator(mode="after")
    def bound_encoded_content(self) -> Self:
        try:
            encoded = json.dumps(
                self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except UnicodeError:
            raise ValueError("NOTE_EDIT_INVALID") from None
        if len(encoded) > MAX_EDIT_BYTES:
            raise ValueError("NOTE_EDIT_LIMIT")
        return self


class EditedDocument(SummaryDocument):
    editor: Literal["user"] = "user"
    personal_notes: str
    edit_disclaimer: str = EDIT_DISCLOSURE


class NoteEditInput(NoteDraftInput):
    base_revision_id: UUID


class NoteCreateInput(NoteDraftInput):
    """An initial human revision binds directly to a real source snapshot."""

    consent: Literal[True]


def request_digest(note_id: UUID, body: NoteDraftInput) -> str:
    # Bind the key to its note as well as all normalized submitted content.
    value = {"note_id": str(note_id), **body.model_dump(mode="json")}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def edited_summary(
    body: NoteDraftInput, base_document: dict, evidence: list[Evidence]
) -> SummaryDocument:
    # Do not accept coverage, limitations, evidence quotations, prompt version
    # or model provenance from the editing client. Original limitations remain.
    value = {
        "title": body.title,
        "overview": body.overview.model_dump(),
        "sections": [section.model_dump() for section in body.sections],
        "limitations": base_document["limitations"],
    }
    full = base_document.get("prompt_version") == "evidence-summary-v2"
    document = parse_summary(json.dumps(value, ensure_ascii=False), evidence, full=full)
    if full:
        document = document.model_copy(update={"prompt_version": "evidence-summary-v2"})
    return document


def render_edited_markdown(document: EditedDocument, evidence: list[Evidence]) -> str:
    markdown = render_markdown(document, evidence, human_edit=True)
    lines = [markdown.rstrip(), "", "## 人工修订与个人理解", "", EDIT_DISCLOSURE]
    if document.personal_notes:
        lines.extend(["", _plain_markdown(document.personal_notes)])
    else:
        lines.extend(["", "本版本未追加个人理解。"])
    return "\n".join(lines) + "\n"
