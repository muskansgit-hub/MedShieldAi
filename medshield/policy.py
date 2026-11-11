"""FSC computation and the action policy (PASS / WARN / REWRITE / BLOCK)."""
from __future__ import annotations

from . import config

ACTIONS = ["PASS", "WARN", "REWRITE", "BLOCK"]


def fsc(confidence: float, incorrectness: float, tier: int) -> float:
    """FSC = C x I x (W / W_max), W_max = 4. Eq. (1) in the paper."""
    return confidence * incorrectness * (tier / 4.0)


def _at_least(a: str, b: str) -> str:
    return a if ACTIONS.index(a) >= ACTIONS.index(b) else b


def decide(score: float, risk, inc) -> tuple[str, list[str]]:
    reasons = []
    if score >= config.BLOCK_AT:
        action = "BLOCK"
        reasons.append(f"FSC {score:.2f} >= block threshold {config.BLOCK_AT}")
    elif score >= config.REWRITE_AT:
        action = "REWRITE"
        reasons.append(f"FSC {score:.2f} >= rewrite threshold {config.REWRITE_AT}")
    elif score >= config.WARN_AT:
        action = "WARN"
        reasons.append(f"FSC {score:.2f} >= warn threshold {config.WARN_AT}")
    else:
        action = "PASS"
        reasons.append(f"FSC {score:.2f} below warn threshold {config.WARN_AT}")

    # Hard safety overrides: these never depend on the threshold settings.
    if risk.tier == 4 and inc.danger >= 0.5:
        action = "BLOCK"
        reasons.append("critical-tier query with explicitly dangerous content")
    if inc.polarity_conflict and risk.tier >= 3:
        action = _at_least(action, "REWRITE")
        reasons.append("answer reassures about a known contraindication" if inc.contraindication
                       else "answer contradicts the verified reference on a high-risk query")
    if risk.crisis:
        if inc.missing_safety > 0:
            action = _at_least(action, "REWRITE")
            reasons.append("possible mental-health crisis and the answer offers no route to help")
        else:
            action = _at_least(action, "WARN")
            reasons.append("possible mental-health crisis: crisis resources attached")
    return action, reasons
