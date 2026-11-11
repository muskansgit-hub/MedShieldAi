from __future__ import annotations

from .base import Provider, ProviderError


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    def chat(self, messages, temperature=0.7, max_tokens=512) -> str:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [m for m in messages if m["role"] != "system"]
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature, "messages": msgs}
        if system:
            body["system"] = system
        data = self._post(
            "https://api.anthropic.com/v1/messages", json=body,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except KeyError as e:
            raise ProviderError(f"unexpected response: {str(data)[:200]}") from e
