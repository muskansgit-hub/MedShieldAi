"""
LLM providers. All share one method: chat(messages, temperature, max_tokens) -> str.

Most hosted and local LLMs speak the OpenAI chat-completions protocol, so one
client covers OpenAI, Groq, NVIDIA NIM, OpenRouter, Together and local Ollama.
Anthropic and Gemini get small native clients. Plain httpx, no vendor SDKs.

Spec strings look like "provider/model", e.g. "ollama/llama3.2",
"nvidia/meta/llama-3.1-8b-instruct", "openai/gpt-4o-mini".
"""
from __future__ import annotations

import os

from .base import Provider, ProviderError
from .openai_compat import OpenAICompatProvider
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .mock import MockProvider

# name -> (base_url, api-key env var, default model)
OPENAI_COMPAT = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.1-8b-instant"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "meta/llama-3.1-8b-instruct"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "meta-llama/llama-3.1-8b-instruct"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY", "meta-llama/Llama-3-8b-chat-hf"),
    "ollama": (os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"), None, "llama3.2"),
}
NATIVE = {
    "anthropic": ("ANTHROPIC_API_KEY", "claude-haiku-5-5"),
    "gemini": ("GEMINI_API_KEY", "gemini-2.0-flash"),
}


def parse_spec(spec: str) -> tuple[str, str | None]:
    if "/" in spec:
        name, model = spec.split("/", 1)
        return name.lower(), model or None
    return spec.lower(), None


def get_provider(spec: str, model: str | None = None) -> Provider:
    name, spec_model = parse_spec(spec)
    model = model or spec_model
    if name == "mock":
        return MockProvider(model or "synthetic")
    if name in OPENAI_COMPAT:
        url, key_env, default = OPENAI_COMPAT[name]
        key = os.getenv(key_env) if key_env else "ollama"
        if key_env and not key:
            raise ProviderError(f"{key_env} is not set")
        return OpenAICompatProvider(name, url, key, model or default)
    if name in NATIVE:
        key_env, default = NATIVE[name]
        key = os.getenv(key_env)
        if not key:
            raise ProviderError(f"{key_env} is not set")
        cls = AnthropicProvider if name == "anthropic" else GeminiProvider
        return cls(key, model or default)
    raise ProviderError(f"unknown provider '{name}'")


def available() -> list[dict]:
    out = [{"name": "mock", "default_model": "synthetic", "configured": True,
            "note": "synthetic responses for testing the pipeline, not a real model"}]
    for name, (url, key_env, default) in OPENAI_COMPAT.items():
        out.append({"name": name, "default_model": default,
                    "configured": key_env is None or bool(os.getenv(key_env)),
                    "note": "local, needs `ollama serve`" if name == "ollama" else key_env})
    for name, (key_env, default) in NATIVE.items():
        out.append({"name": name, "default_model": default, "configured": bool(os.getenv(key_env)), "note": key_env})
    return out


__all__ = ["Provider", "ProviderError", "get_provider", "available", "parse_spec"]
