"""
Linguistic confidence scorer (Approach A from the paper), adapted for runtime use.

Differences from the research code:
  * Returns character spans so the UI can highlight assertive / hedging phrases.
  * Overlapping matches are counted once ("absolutely safe" no longer counts
    as both "absolutely" and "absolutely safe").
  * No batch min-max normalisation. That only works offline over a whole
    benchmark; at request time there is one response, so the smoothed raw score
    C = (H + 0.5) / (H + L + 1) is used directly (neutral text -> 0.5).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .lexicons import HIGH_CONFIDENCE_PATTERNS, LOW_CONFIDENCE_PATTERNS


@dataclass
class Span:
    start: int
    end: int
    text: str
    kind: str  # "assertive" | "hedge"


@dataclass
class ConfidenceResult:
    score: float
    high_count: int
    low_count: int
    spans: list[Span] = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["score"] = round(self.score, 4)
        return d


class ConfidenceScorer:
    def __init__(self):
        self.high = [re.compile(p, re.IGNORECASE) for p in HIGH_CONFIDENCE_PATTERNS]
        self.low = [re.compile(p, re.IGNORECASE) for p in LOW_CONFIDENCE_PATTERNS]

    def _find(self, text: str) -> list[Span]:
        cands: list[Span] = []
        for kind, regs in (("assertive", self.high), ("hedge", self.low)):
            for rx in regs:
                for m in rx.finditer(text):
                    cands.append(Span(m.start(), m.end(), m.group(0), kind))
        # Keep the longest match among overlapping candidates.
        cands.sort(key=lambda s: (-(s.end - s.start), s.start))
        kept: list[Span] = []
        for s in cands:
            if all(s.end <= k.start or s.start >= k.end for k in kept):
                kept.append(s)
        return sorted(kept, key=lambda s: s.start)

    def score(self, text: str) -> ConfidenceResult:
        spans = self._find(text or "")
        h = sum(1 for s in spans if s.kind == "assertive")
        l = sum(1 for s in spans if s.kind == "hedge")
        c = (h + 0.5) / (h + l + 1)
        return ConfidenceResult(score=c, high_count=h, low_count=l, spans=spans)
