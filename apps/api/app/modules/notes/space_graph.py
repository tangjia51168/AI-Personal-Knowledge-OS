"""Bounded space-wide view of saved notes and explicitly human-created links."""

from typing import Any

from sqlalchemy import and_, func, select

from app.modules.identity.access import ActorIdentity, SpaceAccess, recheck_identity
from app.modules.notes.graph import MAX_GRAPH_EDGES, MAX_GRAPH_NODES
from app.modules.notes.models import Note, NoteLink, NoteRevision


def space_graph(access: SpaceAccess) -> dict[str, Any]:
    tenant, session = access.tenant.id, access.actor.session
    latest = (
        select(func.max(NoteRevision.version))
        .where(NoteRevision.tenant_id == tenant, NoteRevision.note_id == Note.id)
        .correlate(Note)
        .scalar_subquery()
    )
    rows = list(
        session.execute(
            select(
                Note.id, NoteRevision.title, NoteRevision.version, NoteRevision.id.label("revision")
            )
            .join(
                NoteRevision,
                and_(
                    NoteRevision.tenant_id == tenant,
                    NoteRevision.note_id == Note.id,
                    NoteRevision.version == latest,
                ),
            )
            .where(Note.tenant_id == tenant)
            .order_by(Note.created_at.desc(), Note.id.desc())
            .limit(MAX_GRAPH_NODES + 1)
        )
    )
    nodes = rows[:MAX_GRAPH_NODES]
    ids = [row.id for row in nodes]
    links = (
        list(
            session.scalars(
                select(NoteLink)
                .where(
                    NoteLink.tenant_id == tenant,
                    NoteLink.active.is_(True),
                    NoteLink.left_note_id.in_(ids),
                    NoteLink.right_note_id.in_(ids),
                )
                .order_by(NoteLink.id)
                .limit(MAX_GRAPH_EDGES + 1)
            )
        )
        if nodes
        else []
    )
    recheck_identity(session, ActorIdentity(access.actor.user.id, access.actor.token_hash))
    reasons = []
    if len(rows) > MAX_GRAPH_NODES:
        reasons.append("node_limit")
    if len(links) > MAX_GRAPH_EDGES:
        reasons.append("edge_limit")
    return {
        "nodes": [
            {
                "id": str(row.id),
                "title": row.title,
                "version": row.version,
                "revision_id": str(row.revision),
            }
            for row in nodes
        ],
        "edges": [
            {
                "id": str(row.id),
                "source": str(row.left_note_id),
                "target": str(row.right_note_id),
                "method": "manual",
                "confidence": None,
            }
            for row in links[:MAX_GRAPH_EDGES]
        ],
        "truncated": bool(reasons),
        "truncation_reasons": reasons,
        "limits": {"nodes": MAX_GRAPH_NODES, "edges": MAX_GRAPH_EDGES},
    }
