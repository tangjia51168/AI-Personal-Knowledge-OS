"""Offline CPU embeddings. Pinned tokenizer/model, masked mean pooling and L2 norm."""

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MANIFEST = json.loads(Path(__file__).with_name("model.json").read_text(encoding="utf-8"))


class Encoder:
    def __init__(self, directory: Path) -> None:
        for name, record in MANIFEST["files"].items():
            path = directory / name
            if path.stat().st_size != record["size"]:
                raise ValueError("MODEL_SIZE_INVALID")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                    raise ValueError("MODEL_DIGEST_INVALID")
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=128)
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(
            str(directory / "onnx/model_quint8_avx2.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.lock = threading.Lock()

    def encode(self, texts: list[str]) -> list[list[float]]:
        with self.lock:
            tokens = self.tokenizer.encode_batch(texts)
            inputs = {
                "input_ids": np.array([item.ids for item in tokens], dtype=np.int64),
                "attention_mask": np.array(
                    [item.attention_mask for item in tokens], dtype=np.int64
                ),
                "token_type_ids": np.array([item.type_ids for item in tokens], dtype=np.int64),
            }
            feeds = {item.name: inputs[item.name] for item in self.model.get_inputs()}
            hidden = self.model.run(None, feeds)[0]
            mask = inputs["attention_mask"][:, :, None]
            pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1)
            normalized = pooled / np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-12)
            if normalized.shape != (len(texts), 384) or not np.isfinite(normalized).all():
                raise ValueError("EMBEDDING_INVALID")
            return normalized.tolist()


def serve() -> None:
    directory = Path(os.environ.get("EMBEDDING_MODEL_DIR", "/model"))
    encoder = Encoder(directory)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass  # Never log texts, credentials, request bodies or original paths.

        def reply(self, code: int, value: dict) -> None:
            body = json.dumps(value, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self.reply(200 if self.path == "/health" else 404, {"model": MANIFEST["identity"]})

        def do_POST(self) -> None:
            if self.path != "/embed" or self.headers.get("Transfer-Encoding"):
                self.reply(404, {"error": "INVALID_REQUEST"})
                return
            try:
                self.connection.settimeout(10)
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 65536:
                    raise ValueError("INVALID_SIZE")
                data = json.loads(self.rfile.read(size))
                texts = data.get("texts")
                if (
                    set(data) != {"texts"}
                    or not isinstance(texts, list)
                    or not 1 <= len(texts) <= 32
                ):
                    raise ValueError("INVALID_TEXTS")
                if any(not isinstance(t, str) or not t or len(t) > 512 for t in texts):
                    raise ValueError("INVALID_TEXT")
                vectors = encoder.encode(texts)
                self.reply(200, {"model": MANIFEST["identity"], "vectors": vectors})
            except (ValueError, TypeError, TimeoutError, OSError):
                self.reply(422, {"error": "EMBEDDING_REQUEST_INVALID"})
            except Exception:
                self.reply(503, {"error": "EMBEDDING_UNAVAILABLE"})

    server = ThreadingHTTPServer((os.environ.get("EMBEDDING_BIND", "127.0.0.1"), 8080), Handler)
    server.serve_forever()


if __name__ == "__main__":
    serve()
