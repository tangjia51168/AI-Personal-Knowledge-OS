"""Bounded explicit local indexing and exact pgvector search of latest revisions."""

import json
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.embedding import MODEL, LocalEmbeddingProvider
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction, recheck_identity
from app.modules.notes.models import NoteRevision

CHUNK_SIZE = 96
MAX_CHUNKS = 128
SPACE_LIMIT = 20000


class IndexInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: UUID
    revision_id: UUID
    consent: bool


class SemanticQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=1, max_length=96)
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("q")
    @classmethod
    def clean_query(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("INVALID_SEMANTIC_QUERY")
        return value.strip()


def chunks(title: str, body: str) -> list[str]:
    content = title + "\n" + body
    return [content[i : i + CHUNK_SIZE] for i in range(0, min(len(content), 12288), CHUNK_SIZE)]


def vector_literal(vector: list[float]) -> str:
    return json.dumps(vector, allow_nan=False, separators=(",", ":"))


def provider() -> LocalEmbeddingProvider:
    try:
        return LocalEmbeddingProvider(get_settings().local_embedding_endpoint)
    except ValueError:
        raise HTTPException(503, detail={"code": "LOCAL_EMBEDDING_NOT_CONFIGURED"}) from None


async def encode(engine: LocalEmbeddingProvider, values: list[str]) -> list[list[float]]:
    try:
        return await engine.encode(values)
    except (ValueError, httpx.HTTPError):
        raise HTTPException(503, detail={"code": "LOCAL_EMBEDDING_UNAVAILABLE"}) from None


def revision(session: Session, tenant: UUID, body: IndexInput) -> NoteRevision:
    row = session.scalar(
        select(NoteRevision).where(
            NoteRevision.tenant_id == tenant,
            NoteRevision.note_id == body.note_id,
            NoteRevision.id == body.revision_id,
        )
    )
    if row is None:
        raise HTTPException(404, detail={"code": "NOTE_REVISION_NOT_FOUND"})
    return row


def indexed_count(session: Session, tenant: UUID, revision_id: UUID) -> int:
    return session.execute(
        text(
            "SELECT count(*) FROM semantic_chunks "
            "WHERE tenant_id=:t AND revision_id=:r AND model=:m"
        ),
        {"t": tenant, "r": revision_id, "m": MODEL},
    ).scalar_one()


def quota(session: Session, tenant: UUID, additional: int) -> None:
    row: Any = session.execute(
        text(
            "SELECT count(*),count(*) FILTER(WHERE created_at>=date_trunc('day',now())) "
            "FROM semantic_chunks WHERE tenant_id=:t"
        ),
        {"t": tenant},
    ).one()
    if row[0] + additional > SPACE_LIMIT or row[1] + additional > 2560:
        raise HTTPException(429, detail={"code": "SEMANTIC_INDEX_QUOTA"})


async def index_revision(identity: ActorIdentity, tenant: UUID, body: IndexInput) -> dict[str, Any]:
    if not body.consent:
        raise HTTPException(422, detail={"code": "LOCAL_EMBEDDING_CONSENT_REQUIRED"})
    engine = provider()
    with authorized_transaction(identity, tenant) as (session, role):
        if role not in ("owner", "admin"):
            raise HTTPException(403, detail={"code": "FORBIDDEN"})
        row = revision(session, tenant, body)
        values = chunks(row.title, row.content_md)
        source = (row.snapshot_id, len(row.title + "\n" + row.content_md))
        already = indexed_count(session, tenant, row.id)
        if already == len(values):
            return index_result(body, len(values), source[1], True)
        if already:
            raise HTTPException(409, detail={"code": "SEMANTIC_INDEX_INCOMPLETE"})
        quota(session, tenant, len(values))
    vectors = []
    # No DB locks while the isolated CPU service works. No automatic retry.
    for start in range(0, len(values), 32):
        with authorized_transaction(identity, tenant) as (_session, role):
            if role not in ("owner", "admin"):
                raise HTTPException(403, detail={"code": "FORBIDDEN"})
        vectors.extend(await encode(engine, values[start : start + 32]))
    with authorized_transaction(identity, tenant) as (session, role):
        if role not in ("owner", "admin"):
            raise HTTPException(403, detail={"code": "FORBIDDEN"})
        row = revision(session, tenant, body)
        if chunks(row.title, row.content_md) != values or row.snapshot_id != source[0]:
            raise HTTPException(409, detail={"code": "SEMANTIC_REVISION_CHANGED"})
        already = indexed_count(session, tenant, row.id)
        if already == len(values):
            return index_result(body, len(values), source[1], True)
        if already:
            raise HTTPException(409, detail={"code": "SEMANTIC_INDEX_INCOMPLETE"})
        quota(session, tenant, len(values))
        for position, (quote, vector) in enumerate(zip(values, vectors, strict=True)):
            session.execute(
                text(
                    "INSERT INTO semantic_chunks(tenant_id,note_id,snapshot_id,revision_id,"
                    "position,model,quote,embedding,created_by) VALUES "
                    "(:t,:n,:s,:r,:p,:m,:q,CAST(:v AS vector),:u)"
                ),
                {
                    "t": tenant,
                    "n": row.note_id,
                    "s": row.snapshot_id,
                    "r": row.id,
                    "p": position,
                    "m": MODEL,
                    "q": quote,
                    "v": vector_literal(vector),
                    "u": identity.user_id,
                },
            )
        record(session, tenant, identity.user_id, AuditAction.NOTE_INDEXED, row.id)
        recheck_identity(session, identity)
    return index_result(body, len(values), source[1], False)


def index_result(body: IndexInput, count: int, chars: int, reused: bool) -> dict[str, Any]:
    return {
        "note_id": str(body.note_id),
        "revision_id": str(body.revision_id),
        "model": MODEL,
        "chunks": count,
        "indexed_characters": min(chars, MAX_CHUNKS * CHUNK_SIZE),
        "total_characters": chars,
        "truncated": chars > MAX_CHUNKS * CHUNK_SIZE,
        "reused": reused,
    }


COVERAGE_SQL = """
WITH latest AS (
  SELECT DISTINCT ON(note_id) id,note_id,title,content_md,version,origin
  FROM note_revisions WHERE tenant_id=:t ORDER BY note_id,version DESC
)
SELECT count(*),count(*) FILTER(WHERE EXISTS(SELECT 1 FROM semantic_chunks c
  WHERE c.tenant_id=:t AND c.revision_id=latest.id AND c.model=:m)),
  count(*) FILTER(WHERE length(title || E'\n' || content_md)>12288)
FROM latest
"""


async def search(identity: ActorIdentity, tenant: UUID, query: SemanticQuery) -> dict[str, Any]:
    with authorized_transaction(identity, tenant):
        pass
    engine = provider()
    vector = (await encode(engine, [query.q]))[0]
    with authorized_transaction(identity, tenant) as (session, _role):
        session.execute(text("SET LOCAL statement_timeout='5s'"))
        total, indexed, long_notes = session.execute(
            text(COVERAGE_SQL), {"t": tenant, "m": MODEL}
        ).one()
        rows = (
            session.execute(
                text("""
            WITH latest AS (
              SELECT DISTINCT ON(note_id) id,note_id,title,version,origin
              FROM note_revisions WHERE tenant_id=:t ORDER BY note_id,version DESC
            ), scored AS (
              SELECT r.note_id,r.id revision_id,r.title,r.version,r.origin,c.quote,c.position,
                1-(c.embedding <=> CAST(:v AS vector)) AS similarity
              FROM semantic_chunks c JOIN latest r ON r.id=c.revision_id AND r.note_id=c.note_id
              WHERE c.tenant_id=:t AND c.model=:m
            ), best AS (
              SELECT DISTINCT ON(note_id) * FROM scored
              ORDER BY note_id,similarity DESC,position
            ) SELECT * FROM best WHERE similarity>=0.35
            ORDER BY similarity DESC,note_id LIMIT :limit
            """),
                {"t": tenant, "m": MODEL, "v": vector_literal(vector), "limit": query.limit},
            )
            .mappings()
            .all()
        )
        recheck_identity(session, identity)
        return {
            "mode": "local_semantic",
            "model": MODEL,
            "coverage": {
                "latest_notes": total,
                "indexed_latest_notes": indexed,
                "long_notes": long_notes,
            },
            "data": [
                {
                    "note_id": str(r["note_id"]),
                    "revision_id": str(r["revision_id"]),
                    "title": r["title"],
                    "version": r["version"],
                    "origin": r["origin"],
                    "snippet": r["quote"],
                    "position": r["position"],
                    "similarity": round(float(r["similarity"]), 6),
                }
                for r in rows
            ],
        }
