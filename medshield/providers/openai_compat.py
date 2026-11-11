from __future__ import annotations

from .base import Provider, ProviderError


class OpenAICompatProvider(Provider):
    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name, self.base_url, self.api_key, self.model = name, base_url.rstrip("/"), api_key, model

    def chat(self, messages, temperature=0.7, max_tokens=512) -> str:
        data = self._post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise ProviderError(f"unexpected response: {str(data)[:200]}") from e
