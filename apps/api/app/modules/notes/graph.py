"""Bounded local views of persisted human links, never inferred relations."""

from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.notes.links import MAX_SPACE_LINKS, require_notes
from app.modules.notes.models import NoteLink, NoteRevision

MAX_GRAPH_NODES = 30
MAX_GRAPH_EDGES = 60


def select_graph(
    root: UUID, pairs: list[tuple[UUID, UUID, UUID]], depth: int
) -> tuple[dict[UUID, int], list[UUID], list[str]]:
    """Stable breadth-first selection, retaining a spanning tree before extra edges."""
    if depth not in (1, 2):
        raise ValueError("GRAPH_DEPTH_INVALID")
    adjacency: dict[UUID, list[tuple[UUID, UUID]]] = defaultdict(list)
    for edge, left, right in pairs:
        adjacency[left].append((right, edge))
        adjacency[right].append((left, edge))
    for neighbors in adjacency.values():
        neighbors.sort()
    distances = {root: 0}
    tree: list[UUID] = []
    frontier = [root]
    reasons: list[str] = []
    for distance in range(1, depth + 1):
        next_frontier = []
        for current in frontier:
            for target, edge in adjacency[current]:
                if target in distances:
                    continue
                if len(distances) >= MAX_GRAPH_NODES:
                    if "node_limit" not in reasons:
                        reasons.append("node_limit")
                    continue
                distances[target] = distance
                next_frontier.append(target)
                tree.append(edge)
        frontier = next_frontier
    tree_ids = set(tree)
    remaining = sorted(
        edge
        for edge, left, right in pairs
        if left in distances and right in distances and edge not in tree_ids
    )
    edges = tree + remaining
    if len(edges) > MAX_GRAPH_EDGES:
        reasons.append("edge_limit")
    return distances, edges[:MAX_GRAPH_EDGES], reasons


def local_graph(session: Session, tenant_id: UUID, root: UUID, depth: int) -> dict[str, Any]:
    require_notes(session, tenant_id, root)
    # Existing writer quotas cap active space links at 1000. Read one bounded
    # edge snapshot (not per-node queries), failing closed if that invariant drifts.
    rows = list(
        session.scalars(
            select(NoteLink)
            .where(NoteLink.tenant_id == tenant_id, NoteLink.active.is_(True))
            .order_by(NoteLink.id)
            .limit(MAX_SPACE_LINKS + 1)
        )
    )
    if len(rows) > MAX_SPACE_LINKS:
        raise HTTPException(409, detail={"code": "GRAPH_INVENTORY_LIMIT"})
    distances, selected, reasons = select_graph(
        root, [(row.id, row.left_note_id, row.right_note_id) for row in rows], depth
    )
    latest = (
        select(NoteRevision.note_id, func.max(NoteRevision.version).label("version"))
        .where(NoteRevision.tenant_id == tenant_id, NoteRevision.note_id.in_(distances))
        .group_by(NoteRevision.note_id)
        .subquery()
    )
    titles = {
        row.note_id: row
        for row in session.execute(
            select(
                NoteRevision.note_id, NoteRevision.title, NoteRevision.version, NoteRevision.origin
            )
            .join(
                latest,
                (latest.c.note_id == NoteRevision.note_id)
                & (latest.c.version == NoteRevision.version),
            )
            .where(NoteRevision.tenant_id == tenant_id)
        )
    }
    if set(titles) != set(distances):
        raise HTTPException(409, detail={"code": "GRAPH_REVISION_MISSING"})
    by_id = {row.id: row for row in rows}
    return {
        "root_id": str(root),
        "depth": depth,
        "nodes": [
            {
                "id": str(note_id),
                "title": titles[note_id].title,
                "version": titles[note_id].version,
                "origin": titles[note_id].origin,
                "distance": distance,
            }
            for note_id, distance in distances.items()
        ],
        "edges": [
            {
                "id": str(edge),
                "source": str(by_id[edge].left_note_id),
                "target": str(by_id[edge].right_note_id),
                "created_by": str(by_id[edge].created_by),
                "updated_by": str(by_id[edge].updated_by),
                "created_at": by_id[edge].created_at.isoformat(),
                "updated_at": by_id[edge].updated_at.isoformat(),
                "method": "manual",
                "confidence": None,
            }
            for edge in selected
        ],
        "truncated": bool(reasons),
        "truncation_reasons": reasons,
        "limits": {"nodes": MAX_GRAPH_NODES, "edges": MAX_GRAPH_EDGES},
    }
