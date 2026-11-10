"""Embedding contract rejects untrusted output; deterministic index text semantics."""

import math

import pytest
from app.integrations.embedding import MODEL, LocalEmbeddingProvider, validate_vectors
from app.modules.notes.semantic import SemanticQuery, chunks, vector_literal
from pydantic import ValidationError


def test_index_chunks_are_exact_contiguous_bounded_revision_text():
    title, body = "不可变标题😀", "正文材料。" * 4000
    rows = chunks(title, body)
    assert len(rows) == 128
    assert "".join(rows) == (title + "\n" + body)[:12288]
    assert all(len(row) == 96 for row in rows)
    assert "".join(chunks("短标题", "短正文")) == "短标题\n短正文"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://embedding:8080",
        "http://embedding:8080/path",
        "http://example.com:8080",
        "http://169.254.169.254:8080",
        "http://user:secret@embedding:8080",
        "http://embedding:8080?proxy=x",
        "http://embedding:8081",
        "http://embedding:8080\n",
        "",
    ],
)
def test_provider_rejects_nonlocal_or_credentialed_endpoint(endpoint):
    with pytest.raises(ValueError):
        LocalEmbeddingProvider(endpoint)


def test_provider_contract_pins_model_dimensions_normalization_and_count():
    vector = [1.0] + [0.0] * 383
    assert validate_vectors({"model": MODEL, "vectors": [vector]}, 1) == [vector]
    assert LocalEmbeddingProvider("http://embedding:8080/").endpoint == "http://embedding:8080"
    for value in (
        {"model": "other", "vectors": [vector]},
        {"model": MODEL, "vectors": [vector[:-1]]},
        {"model": MODEL, "vectors": [[math.nan] + vector[1:]]},
        {"model": MODEL, "vectors": [[True] + vector[1:]]},
        {"model": MODEL, "vectors": [[0.0] * 384]},
        {"model": MODEL, "vectors": []},
    ):
        with pytest.raises(ValueError):
            validate_vectors(value, 1)
    with pytest.raises(ValueError):
        vector_literal([math.inf])


@pytest.mark.parametrize(
    "data",
    [
        {"q": " "},
        {"q": "x\ny"},
        {"q": "a" * 97},
        {"q": "abc", "tenant_id": "spoof"},
        {"q": "a", "limit": 21},
    ],
)
def test_semantic_query_has_bounded_explicit_contract(data):
    with pytest.raises(ValidationError):
        SemanticQuery.model_validate(data)
