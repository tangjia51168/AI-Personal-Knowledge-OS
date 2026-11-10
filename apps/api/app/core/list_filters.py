"""Bounded literal matching and keyset query parameters for content lists."""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.integrations.platforms.detection import Platform


class ContentListQuery(BaseModel):
    q: str = Field(default="", max_length=120)
    platform: Platform | None = None
    cursor: UUID | None = None
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("q", mode="before")
    @classmethod
    def trim_query(cls, value: str) -> str:
        return value.strip()

    @field_validator("q")
    @classmethod
    def bound_utf8(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 480:
            raise ValueError("CONTENT_QUERY_LIMIT")
        return value


def literal_pattern(value: str) -> str:
    # An explicit non-backslash escape leaves user backslashes literal too.
    return "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
