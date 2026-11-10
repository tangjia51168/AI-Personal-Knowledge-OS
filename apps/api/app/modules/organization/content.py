"""Bounded, deterministic label names; no automatic knowledge assertions."""

import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_name(value: str) -> str:
    # Reject controls before NFKC so tabs/newlines cannot be silently folded
    # into a different label, and reject format characters such as bidi marks.
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value):
        raise ValueError("LABEL_NAME_CONTROL_CHARACTER")
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 1 <= len(value) <= 60 or len(value.encode("utf-8")) > 240:
        raise ValueError("LABEL_NAME_LIMIT")
    return value


class LabelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["topic", "tag"]
    name: str = Field(min_length=1, max_length=1024)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        return normalize_name(value)

    @property
    def name_key(self) -> str:
        return self.name.casefold()


class LabelUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=1024)
    active: bool | None = Field(default=None, strict=True)
    expected_version: int = Field(ge=1, le=2_147_483_646, strict=True)
    parent_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str | None) -> str | None:
        return normalize_name(value) if value is not None else None

    @property
    def name_key(self) -> str | None:
        return self.name.casefold() if self.name is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "LabelUpdateInput":
        if not self.model_fields_set.intersection({"name", "active", "parent_id"}):
            raise ValueError("LABEL_UPDATE_EMPTY")
        if any(getattr(self, key) is None for key in self.model_fields_set - {"parent_id"}):
            raise ValueError("LABEL_UPDATE_NULL")
        return self
