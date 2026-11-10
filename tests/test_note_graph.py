"""Pure deterministic selection tests; real persistence is tested separately."""

from itertools import combinations
from uuid import UUID

import pytest
from app.modules.notes.graph import select_graph


def uid(value):
    return UUID(int=value)


def pairs(connections):
    return [(uid(1000 + i), uid(a), uid(b)) for i, (a, b) in enumerate(connections)]


def test_isolated_center():
    assert select_graph(uid(1), [], 2) == ({uid(1): 0}, [], [])


def test_depth_and_induced_cycle_edges():
    edges = pairs([(1, 2), (1, 3), (2, 3), (3, 4), (4, 5), (6, 7)])
    nodes, selected, reasons = select_graph(uid(1), edges, 1)
    assert nodes == {uid(1): 0, uid(2): 1, uid(3): 1}
    assert set(selected) == {edge[0] for edge in edges[:3]}
    nodes, selected, reasons = select_graph(uid(1), edges, 2)
    assert nodes == {uid(1): 0, uid(2): 1, uid(3): 1, uid(4): 2}
    assert len(selected) == 4 and reasons == []


def test_selection_deterministic_under_inventory_reordering():
    edges = pairs([(1, 4), (4, 5), (1, 2), (2, 3), (2, 5), (3, 4)])
    assert select_graph(uid(1), edges, 2) == select_graph(uid(1), edges[::-1], 2)


def test_node_cap_and_exact_boundary():
    edges = pairs([(1, value) for value in range(2, 32)])
    nodes, selected, reasons = select_graph(uid(1), edges, 1)
    assert list(nodes) == [uid(value) for value in range(1, 31)]
    assert len(selected) == 29 and reasons == ["node_limit"]
    assert select_graph(uid(1), edges[:-1], 1)[2] == []


def test_edge_cap_preserves_connectivity_before_extra_edges():
    edges = pairs(list(combinations(range(1, 31), 2)))
    nodes, selected, reasons = select_graph(uid(30), edges, 2)
    assert len(nodes) == 30 and len(selected) == 60 and reasons == ["edge_limit"]
    chosen = {edge[0]: edge[1:] for edge in edges}
    reached = {uid(30)}
    for edge in selected[:29]:
        a, b = chosen[edge]
        assert a in reached or b in reached
        reached.update((a, b))
    assert reached == set(nodes)


@pytest.mark.parametrize("depth", [0, 3, -1])
def test_invalid_depth(depth):
    with pytest.raises(ValueError, match="GRAPH_DEPTH_INVALID"):
        select_graph(uid(1), [], depth)
