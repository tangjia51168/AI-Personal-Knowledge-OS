"""Operator-selected local embedding boundary; no cloud fallback or user URLs."""

import json
import math
from urllib.parse import urlsplit

import httpx

MODEL = "multilingual-minilm-l12-v2-quint8-avx2-e8f8c211-mean128-v1"
DIMENSIONS = 384


def validate_vectors(value: object, count: int) -> list[list[float]]:
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise ValueError("EMBEDDING_MODEL_MISMATCH")
    vectors = value.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != count:
        raise ValueError("EMBEDDING_RESPONSE_INVALID")
    result = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != DIMENSIONS:
            raise ValueError("EMBEDDING_DIMENSIONS_INVALID")
        if any(type(v) not in (float, int) or not math.isfinite(v) for v in vector):
            raise ValueError("EMBEDDING_NONFINITE")
        norm = sum(v * v for v in vector)
        if not 0.999 <= norm <= 1.001:
            raise ValueError("EMBEDDING_NORMALIZATION_INVALID")
        result.append([float(v) for v in vector])
    return result


class LocalEmbeddingProvider:
    def __init__(self, endpoint: str) -> None:
        # Only the isolated compose service or host loopback. No arbitrary SSRF.
        if any(ord(c) <= 32 or ord(c) == 127 for c in endpoint):
            raise ValueError("LOCAL_EMBEDDING_NOT_CONFIGURED")
        url = urlsplit(endpoint)
        if (
            url.scheme != "http"
            or url.hostname not in ("embedding", "127.0.0.1")
            or url.port != 8080
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ValueError("LOCAL_EMBEDDING_NOT_CONFIGURED")
        self.endpoint = endpoint.rstrip("/")

    async def encode(self, texts: list[str]) -> list[list[float]]:
        if not 1 <= len(texts) <= 32 or any(not t or len(t) > 512 for t in texts):
            raise ValueError("EMBEDDING_INPUT_INVALID")
        async with httpx.AsyncClient(timeout=45, trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", self.endpoint + "/embed", json={"texts": texts}) as r:
                r.raise_for_status()
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 512 * 1024:
                        raise ValueError("EMBEDDING_RESPONSE_TOO_LARGE")
                return validate_vectors(json.loads(body), len(texts))
