"""Bounded DeepSeek summary provider, separate from the short connection probe."""

from dataclasses import dataclass

from pydantic import SecretStr

from app.integrations.ai import Generation, ProviderError, request_json, token_count


@dataclass
class DeepSeekSummaryProvider:
    model: str
    api_key: SecretStr

    async def generate(self, prompt: str) -> Generation:
        if not prompt or len(prompt) > 24000:
            raise ProviderError("PROVIDER_INPUT_LIMIT")
        if not self.api_key.get_secret_value():
            raise ProviderError("PROVIDER_KEY_REQUIRED")
        result = await request_json(
            "https://api.deepseek.com/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": 2048,
                # DeepSeek enables thinking by default. The fixed short output
                # allowance is for the structured note, not hidden reasoning.
                # https://api-docs.deepseek.com/guides/thinking_mode/
                "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"},
            },
            self.api_key,
            total_timeout=60,
        )
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        choice = choices[0]
        if choice.get("finish_reason") != "stop":
            raise ProviderError("PROVIDER_RESPONSE_INCOMPLETE", True)
        message, usage = choice.get("message"), result.get("usage")
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
        ):
            raise ProviderError("PROVIDER_RESPONSE_INVALID", True)
        if not isinstance(usage, dict):
            raise ProviderError("PROVIDER_USAGE_INVALID", True)
        input_tokens = token_count(usage.get("prompt_tokens"))
        output_tokens = token_count(usage.get("completion_tokens"))
        if (
            input_tokens is None
            or input_tokens < 1
            or output_tokens is None
            or not (1 <= output_tokens <= 2048)
        ):
            raise ProviderError("PROVIDER_USAGE_INVALID", True)
        return Generation(message["content"], input_tokens, output_tokens)
