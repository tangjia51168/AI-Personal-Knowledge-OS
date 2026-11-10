"""Bounded REST providers; no arbitrary cloud URL, redirects, proxy or retry.

https://developers.openai.com/api/reference/resources/responses/methods/create
https://api-docs.deepseek.com/api/create-chat-completion
https://docs.ollama.com/api/generate
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.core.config import get_settings

TOTAL_TIMEOUT = 25
RESPONSE_LIMIT = 65536
# Only explicit request/authentication/rate rejections prove no generation was
# accepted. A timeout, redirect or unexpected success code may follow a charge.
DEFINITE_REJECTIONS = frozenset({400, 401, 403, 404, 405, 406, 413, 415, 422, 429})


class ProviderError(Exception):
    def __init__(
        self, code: str, outcome_unknown: bool = False, *, http_status: int | None = None
    ) -> None:
        self.code, self.outcome_unknown = code, outcome_unknown
        self.http_status = http_status
        super().__init__(code)


@dataclass
class Generation:
    text: str
    input_tokens: int | None
    output_tokens: int | None


class Provider(Protocol):
    async def generate(self, prompt: str) -> Generation: ...


def token_count(value: Any) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ProviderError("PROVIDER_USAGE_INVALID", True)
    return value


async def request_json(
    url: str,
    payload: dict[str, Any],
    key: SecretStr | None = None,
    *,
    total_timeout: int = TOTAL_TIMEOUT,
) -> dict:
    if not 1 <= total_timeout <= 60:
        raise ProviderError("PROVIDER_TIMEOUT_LIMIT")
    try:
        async with asyncio.timeout(total_timeout):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(total_timeout, connect=3),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                headers = {"Accept-Encoding": "identity"}
                if key:
                    headers["Authorization"] = f"Bearer {key.get_secret_value()}"
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        raise ProviderError(
                            "PROVIDER_HTTP_ERROR",
                            response.status_code not in DEFINITE_REJECTIONS,
                            http_status=response.status_code,
                        )
                    # Reject compression before reading; a small compressed
                    # chunk can expand before a decoded-byte limit is checked.
                    if response.headers.get("Content-Encoding", "identity").strip().lower() != (
                        "identity"
                    ):
                        raise ProviderError("PROVIDER_RESPONSE_ENCODING", True)
                    data = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(chunk) > RESPONSE_LIMIT - len(data):
                            raise ProviderError("PROVIDER_RESPONSE_LIMIT", True)
                        data.extend(chunk)
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        return result
    except ProviderError:
        raise
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise ProviderError("PROVIDER_CONNECT_FAILED") from None
    except (TimeoutError, httpx.TimeoutException):
        raise ProviderError("PROVIDER_TIMEOUT", True) from None
    except Exception:
        raise ProviderError("PROVIDER_OUTCOME_UNKNOWN", True) from None


def validate_prompt(prompt: str) -> None:
    if not prompt or len(prompt) > 4000:
        raise ProviderError("PROVIDER_INPUT_LIMIT")


@dataclass
class OllamaProvider:
    model: str
    endpoint_name: str

    async def generate(self, prompt: str) -> Generation:
        validate_prompt(prompt)
        endpoint = get_settings().ollama_endpoints.get(self.endpoint_name)
        if not endpoint:
            raise ProviderError("LOCAL_ENDPOINT_NOT_AUTHORIZED")
        url = urlsplit(endpoint)
        if (
            url.scheme not in ("http", "https")
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not url.hostname
        ):
            raise ProviderError("LOCAL_ENDPOINT_INVALID")
        result = await request_json(
            endpoint.rstrip("/") + "/api/generate",
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 256},
            },
        )
        if result.get("done") is not True or result.get("done_reason") != "stop":
            raise ProviderError("PROVIDER_RESPONSE_INCOMPLETE", True)
        if not isinstance(result.get("response"), str) or not result["response"].strip():
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        return Generation(
            result["response"],
            token_count(result.get("prompt_eval_count")),
            token_count(result.get("eval_count")),
        )


@dataclass
class OpenAIProvider:
    model: str
    api_key: SecretStr

    async def generate(self, prompt: str) -> Generation:
        validate_prompt(prompt)
        if not self.api_key.get_secret_value():
            raise ProviderError("PROVIDER_KEY_REQUIRED")
        result = await request_json(
            "https://api.openai.com/v1/responses",
            {
                "model": self.model,
                "input": prompt,
                "store": False,
                "stream": False,
                "max_output_tokens": 256,
            },
            self.api_key,
        )
        if result.get("status") != "completed":
            raise ProviderError("PROVIDER_RESPONSE_INCOMPLETE", True)
        output = result.get("output")
        if not isinstance(output, list):
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        texts = []
        for item in output:
            if not isinstance(item, dict):
                raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
            if item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
            for part in content:
                if not isinstance(part, dict):
                    raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
                if part.get("type") == "refusal":
                    raise ProviderError("PROVIDER_REFUSAL", True)
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        text = "\n".join(texts)
        if not text.strip():
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        usage = result.get("usage")
        if not isinstance(usage, dict):
            raise ProviderError("PROVIDER_USAGE_INVALID", True)
        return Generation(
            text,
            token_count(usage.get("input_tokens")),
            token_count(usage.get("output_tokens")),
        )


@dataclass
class DeepSeekProvider:
    """DeepSeek's fixed OpenAI-compatible Chat Completions endpoint."""

    model: str
    api_key: SecretStr

    async def generate(self, prompt: str) -> Generation:
        validate_prompt(prompt)
        if not self.api_key.get_secret_value():
            raise ProviderError("PROVIDER_KEY_REQUIRED")
        result = await request_json(
            "https://api.deepseek.com/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": 256,
            },
            self.api_key,
        )
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        if first.get("finish_reason") != "stop":
            raise ProviderError("PROVIDER_RESPONSE_INCOMPLETE", True)
        message = first.get("message")
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
        ):
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        usage = result.get("usage")
        if not isinstance(usage, dict):
            raise ProviderError("PROVIDER_USAGE_INVALID", True)
        return Generation(
            message["content"],
            token_count(usage.get("prompt_tokens")),
            token_count(usage.get("completion_tokens")),
        )
