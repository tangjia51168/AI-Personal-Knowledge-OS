"""Protocol tests only. MockTransport is not evidence of real inference."""

import asyncio
import gzip
import json
from types import SimpleNamespace

import httpx
import pytest
from app.integrations import ai
from pydantic import SecretStr


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient

    def streaming_handler(request):
        response = handler(request)
        # Response(json=...) is already consumed by HTTPX. Rebuild it as a raw
        # stream so protocol fixtures exercise the same path as network I/O.
        if response.is_stream_consumed:
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=httpx.ByteStream(response.content),
            )
        return response

    monkeypatch.setattr(
        ai.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(streaming_handler),
        ),
    )


def response_data():
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "protocol-only"},
                ],
            }
        ],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def test_responses_fixed_url_privacy_and_real_usage_shape(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key-never-sent"
        assert request.headers["accept-encoding"] == "identity"
        data = json.loads(request.content)
        assert data["store"] is False and data["stream"] is False
        assert data["max_output_tokens"] == 256 and data["model"] == "user-selected-model"
        return httpx.Response(200, json=response_data())

    install_transport(monkeypatch, handler)
    result = asyncio.run(
        ai.OpenAIProvider("user-selected-model", SecretStr("test-key-never-sent")).generate("test")
    )
    assert result.text == "protocol-only" and result.input_tokens == 3 and result.output_tokens == 2


def test_deepseek_fixed_url_chat_shape_and_usage(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer deepseek-key-never-sent"
        data = json.loads(request.content)
        assert data["stream"] is False and data["max_tokens"] == 256
        assert data["messages"] == [{"role": "user", "content": "test"}]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "deepseek-protocol-only"}}
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 5},
            },
        )

    install_transport(monkeypatch, handler)
    result = asyncio.run(
        ai.DeepSeekProvider("deepseek-chat", SecretStr("deepseek-key-never-sent")).generate("test")
    )
    assert result.text == "deepseek-protocol-only"
    assert result.input_tokens == 4 and result.output_tokens == 5


@pytest.mark.parametrize("text", ["", "  \r\n\t"])
def test_openai_blank_completed_output_is_unknown(monkeypatch, text):
    data = response_data()
    data["output"][0]["content"][0]["text"] = text
    install_transport(monkeypatch, lambda _: httpx.Response(200, json=data))
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.OpenAIProvider("test", SecretStr("unused-key")).generate("test"))
    assert error.value.code == "PROVIDER_RESPONSE_INVALID"
    assert error.value.outcome_unknown is True


def ollama_data():
    return {
        "done": True,
        "done_reason": "stop",
        "response": "protocol-only",
        "prompt_eval_count": 4,
        "eval_count": 3,
    }


def install_ollama(monkeypatch, data):
    monkeypatch.setattr(
        ai,
        "get_settings",
        lambda: SimpleNamespace(ollama_endpoints={"authorized": "http://protocol.invalid"}),
    )
    install_transport(monkeypatch, lambda _: httpx.Response(200, json=data))


def test_ollama_completed_stop_preserves_real_usage_shape(monkeypatch):
    install_ollama(monkeypatch, ollama_data())
    result = asyncio.run(ai.OllamaProvider("test", "authorized").generate("test"))
    assert result.text == "protocol-only"
    assert result.input_tokens == 4 and result.output_tokens == 3


@pytest.mark.parametrize("reason", ["length", "load", "unload", "unknown", None])
def test_ollama_truncated_or_unknown_done_reason_stops_fallback(monkeypatch, reason):
    data = ollama_data()
    data["done_reason"] = reason
    install_ollama(monkeypatch, data)
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.OllamaProvider("test", "authorized").generate("test"))
    assert error.value.code == "PROVIDER_RESPONSE_INCOMPLETE"
    assert error.value.outcome_unknown is True


@pytest.mark.parametrize("text", ["", "  \r\n\t"])
def test_ollama_blank_completed_output_is_unknown(monkeypatch, text):
    data = ollama_data()
    data["response"] = text
    install_ollama(monkeypatch, data)
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.OllamaProvider("test", "authorized").generate("test"))
    assert error.value.code == "PROVIDER_RESPONSE_INVALID"
    assert error.value.outcome_unknown is True


@pytest.mark.parametrize("encoding", ["gzip", " GZip ", "br", "deflate", "identity,gzip", ""])
def test_compressed_response_is_rejected_before_body_read(monkeypatch, encoding):
    compressed = gzip.compress(b"x" * (8 * 1024 * 1024))
    assert len(compressed) < ai.RESPONSE_LIMIT

    class CompressedStream(httpx.AsyncByteStream):
        read = False

        async def __aiter__(self):
            self.read = True
            yield compressed

    stream = CompressedStream()
    install_transport(
        monkeypatch,
        lambda _: httpx.Response(200, headers={"Content-Encoding": encoding}, stream=stream),
    )
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.request_json("https://protocol.invalid", {}))
    assert error.value.code == "PROVIDER_RESPONSE_ENCODING"
    assert error.value.outcome_unknown is True
    assert stream.read is False


def test_raw_byte_limit_stops_before_accumulating_extra_chunks(monkeypatch):
    class OversizedStream(httpx.AsyncByteStream):
        chunks_read = 0

        async def __aiter__(self):
            for value in (b"x" * ai.RESPONSE_LIMIT, b"x", b"must not be read"):
                self.chunks_read += 1
                yield value

    stream = OversizedStream()
    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=stream))
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.request_json("https://protocol.invalid", {}))
    assert error.value.code == "PROVIDER_RESPONSE_LIMIT"
    assert error.value.outcome_unknown is True
    assert stream.chunks_read == 2


def test_identity_raw_response_at_exact_limit_is_accepted(monkeypatch):
    data = b'{"value":"' + b"x" * (ai.RESPONSE_LIMIT - 12) + b'"}'
    assert len(data) == ai.RESPONSE_LIMIT
    install_transport(monkeypatch, lambda _: httpx.Response(200, content=data))
    assert asyncio.run(ai.request_json("https://protocol.invalid", {})) == {
        "value": "x" * (ai.RESPONSE_LIMIT - 12)
    }


def test_summary_transport_timeout_is_unknown_and_keeps_fixed_bounds(monkeypatch):
    def timeout(_request):
        raise httpx.ReadTimeout("must never be exposed")

    install_transport(monkeypatch, timeout)
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.request_json("https://protocol.invalid", {}, total_timeout=60))
    assert error.value.code == "PROVIDER_TIMEOUT" and error.value.outcome_unknown
    with pytest.raises(ai.ProviderError, match="PROVIDER_TIMEOUT_LIMIT"):
        asyncio.run(ai.request_json("https://protocol.invalid", {}, total_timeout=61))


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", None])
def test_deepseek_incomplete_outcomes_stop_fallback(monkeypatch, finish_reason):
    install_transport(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": finish_reason, "message": {"content": "partial"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            },
        ),
    )
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.DeepSeekProvider("deepseek-chat", SecretStr("unused-key")).generate("test"))
    assert error.value.code == "PROVIDER_RESPONSE_INCOMPLETE"
    assert error.value.outcome_unknown is True


@pytest.mark.parametrize("variant", ["empty", "whitespace", "invalid_tokens", "invalid_usage"])
def test_deepseek_invalid_output_and_usage_are_unknown(monkeypatch, variant):
    data = {
        "choices": [{"finish_reason": "stop", "message": {"content": "output"}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3},
    }
    if variant == "empty":
        data["choices"][0]["message"]["content"] = ""
    elif variant == "whitespace":
        data["choices"][0]["message"]["content"] = "  "
    elif variant == "invalid_tokens":
        data["usage"]["completion_tokens"] = True
    else:
        data["usage"] = []
    install_transport(monkeypatch, lambda _: httpx.Response(200, json=data))
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.DeepSeekProvider("deepseek-chat", SecretStr("unused-key")).generate("test"))
    assert error.value.outcome_unknown is True


@pytest.mark.parametrize("variant", ["incomplete", "refusal", "invalid_tokens", "oversized"])
def test_uncertain_outcomes_stop_fallback(monkeypatch, variant):
    data = response_data()
    if variant == "incomplete":
        data["status"] = "incomplete"
    elif variant == "refusal":
        data["output"][0]["content"] = [{"type": "refusal", "refusal": "rejected"}]
    elif variant == "invalid_tokens":
        data["usage"]["input_tokens"] = True
    else:
        data["output"][0]["content"][0]["text"] = "x" * 70000
    install_transport(monkeypatch, lambda _: httpx.Response(200, json=data))
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.OpenAIProvider("test", SecretStr("unused-key")).generate("test"))
    assert error.value.outcome_unknown is True


def test_total_deadline_cancels_slow_provider(monkeypatch):
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            yield b"{}"

    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=SlowStream()))
    monkeypatch.setattr(ai, "TOTAL_TIMEOUT", 0.01)
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.OpenAIProvider("test", SecretStr("unused-key")).generate("test"))
    assert error.value.outcome_unknown is True


@pytest.mark.parametrize("status", [201, 202, 204, 301, 302, 307, 308, 408, 409, 499, 500, 503])
def test_ambiguous_http_response_never_permits_another_billable_attempt(monkeypatch, status):
    install_transport(
        monkeypatch,
        lambda _: httpx.Response(status, headers={"Location": "https://never-follow.invalid"}),
    )
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.DeepSeekProvider("deepseek-chat", SecretStr("unused-key")).generate("test"))
    assert error.value.code == "PROVIDER_HTTP_ERROR"
    assert error.value.outcome_unknown is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 406, 413, 415, 422, 429])
def test_explicit_rejection_allows_authorized_fallback(monkeypatch, status):
    install_transport(monkeypatch, lambda _: httpx.Response(status))
    with pytest.raises(ai.ProviderError) as error:
        asyncio.run(ai.DeepSeekProvider("deepseek-chat", SecretStr("unused-key")).generate("test"))
    assert error.value.code == "PROVIDER_HTTP_ERROR"
    assert error.value.outcome_unknown is False
