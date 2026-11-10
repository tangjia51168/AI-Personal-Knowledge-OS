"""Database-only source filters; no collector, DNS or external side effect."""

from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select, tuple_
from sqlalchemy.sql.elements import ColumnElement

from app.core.list_filters import ContentListQuery, literal_pattern
from app.integrations.platforms.detection import PLATFORM_DOMAINS
from app.modules.identity.access import SpaceAccess
from app.modules.sources.models import Source
from app.modules.sources.service import represent


class SourceListQuery(ContentListQuery):
    status: Literal["accepted", "rejected"] | None = None


def source_platform() -> ColumnElement[str | None]:
    # Canonical URLs are lower-case ASCII hosts without credentials and always
    # have a slash after their authority. Nonstandard ports remain recognizable;
    # this classification grants no collector permission. IPv6 falls back to web.
    host = func.split_part(func.split_part(Source.canonical_url, "/", 3), ":", 1)
    return case(
        (Source.canonical_url.is_(None), None),
        *[
            (or_(*[or_(host == domain, host.endswith("." + domain)) for domain in domains]), name)
            for name, domains in PLATFORM_DOMAINS
        ],
        else_="web",
    )


def list_page(access: SpaceAccess, params: SourceListQuery) -> dict[str, Any]:
    statement = select(Source).where(Source.tenant_id == access.tenant.id)
    if params.q:
        pattern = literal_pattern(params.q)
        statement = statement.where(
            or_(
                Source.original_url.ilike(pattern, escape="!"),
                Source.canonical_url.ilike(pattern, escape="!"),
            )
        )
    if params.platform is not None:
        statement = statement.where(source_platform() == params.platform)
    if params.status is not None:
        statement = statement.where(Source.status == params.status)
    if params.cursor is not None:
        anchor = access.actor.session.scalar(statement.where(Source.id == params.cursor))
        if anchor is None:
            raise HTTPException(404, detail={"code": "SOURCE_CURSOR_NOT_FOUND"})
        statement = statement.where(
            tuple_(Source.created_at, Source.id) < (anchor.created_at, anchor.id)
        )
    rows = list(
        access.actor.session.scalars(
            statement.order_by(Source.created_at.desc(), Source.id.desc()).limit(params.limit + 1)
        )
    )
    page = rows[: params.limit]
    return {
        "data": [represent(row) for row in page],
        "next_cursor": str(page[-1].id) if len(rows) > params.limit else None,
    }
