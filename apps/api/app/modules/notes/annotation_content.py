"""Bounded, version-specific manual annotations; no model inference."""

import hashlib
import json
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

AnnotationKind = Literal["overview", "core_claims", "steps", "examples", "numbers", "takeaways"]


class AnnotationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: UUID
    kind: AnnotationKind
    item_index: int = Field(ge=0, le=5, strict=True)
    expected_quote: str = Field(min_length=1, max_length=1200)
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("expected_quote")
    @classmethod
    def quote_utf8(cls, value: str) -> str:
        value.encode("utf-8")
        return value

    @field_validator("body")
    @classmethod
    def safe_body(cls, value: str) -> str:
        value = value.strip()
        if (
            not value
            or len(value.encode("utf-8")) > 8000
            or any(unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\t" for c in value)
        ):
            raise ValueError("ANNOTATION_TEXT_INVALID")
        return value

    def digest(self, note_id: UUID, revision_id: UUID) -> str:
        payload = {
            **self.model_dump(mode="json", exclude={"idempotency_key"}),
            "note_id": str(note_id),
            "revision_id": str(revision_id),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()


def paragraph(document: dict, kind: str, item_index: int) -> str:
    """Extract from the immutable server document, never a client supplied quote."""
    if kind == "overview":
        item = document.get("overview") if item_index == 0 else None
    else:
        sections = document.get("sections", [])
        section = next((s for s in sections if s.get("kind") == kind), None)
        items = section.get("items", []) if section is not None else []
        item = items[item_index] if 0 <= item_index < len(items) else None
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        raise ValueError("ANNOTATION_ANCHOR_INVALID")
    return item["text"]
