"""Immutable manual definitions anchored to one exact revision evidence item."""

import hashlib
import json
import unicodedata
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.organization.content import normalize_name


class ConceptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: UUID
    name: str = Field(min_length=1, max_length=1024)
    definition: str = Field(min_length=1, max_length=2000)
    evidence_key: str = Field(pattern=r"^E[0-9]{3}$")
    expected_quote: str = Field(min_length=1, max_length=800)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        return normalize_name(value)

    @field_validator("definition")
    @classmethod
    def definition_text(cls, value: str) -> str:
        value = value.strip()
        if (
            not value
            or len(value.encode("utf-8")) > 8000
            or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} and c not in "\n\t" for c in value)
        ):
            raise ValueError("CONCEPT_DEFINITION_INVALID")
        return value

    @field_validator("expected_quote")
    @classmethod
    def quote_text(cls, value: str) -> str:
        value.encode("utf-8")
        return value

    def digest(self, note: UUID, revision: UUID) -> str:
        payload = {
            **self.model_dump(mode="json", exclude={"idempotency_key"}),
            "note_id": str(note),
            "revision_id": str(revision),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
