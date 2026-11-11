"""
Deterministic mock provider for tests and key-free demos.

It is NOT a model. It returns one of three canned answer styles, picked by a
hash of the question, so the guard pipeline can be exercised end to end
offline. Anything produced with it is labelled synthetic.
"""
from __future__ import annotations

import hashlib
import json

from .base import Provider

STYLES = {
    "overconfident": "Yes, absolutely. It is perfectly safe and there is no risk at all, so don't worry. "
                     "You should just take the usual amount and you'll be fine.",
    "hedged": "It depends on your situation. In many cases this is manageable, however it could "
              "potentially cause problems for some people. I'd recommend you speak to a pharmacist "
              "or doctor who knows your history before deciding.",
    "plain": "This is common and usually improves with rest, fluids and time. Most people recover "
             "within a few days.",
}


class MockProvider(Provider):
    name = "mock"

    def __init__(self, model: str = "synthetic"):
        self.model = model

    def chat(self, messages, temperature=0.7, max_tokens=512) -> str:
        text = messages[-1]["content"]
        system = " ".join(m["content"] for m in messages if m["role"] == "system")
        if "medical safety editor" in system:
            return ("This may be reasonable for some people, but it depends on your health history and "
                    "other medicines. Please check with a doctor or pharmacist before acting on it.")
        if "clinical safety reviewer" in text:
            risky = any(w in text.lower() for w in ("perfectly safe", "no risk", "double"))
            return json.dumps({"incorrectness": 0.8 if risky else 0.2, "reason": "mock judge"})
        if self.model in STYLES:
            return STYLES[self.model]
        h = int(hashlib.sha256(text.encode()).hexdigest(), 16) % 3
        return STYLES[["overconfident", "hedged", "plain"][h]]
