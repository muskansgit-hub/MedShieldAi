"""
MedShield guard: the orchestration layer.

    query --> RiskClassifier --> tier W, crisis flag
          --> ReferenceIndex --> closest verified answer (if similar enough)
    response --> ConfidenceScorer --> C (+ highlighted spans)
             --> IncorrectnessScorer --> I (+ components)
    FSC = C x I x W/4 --> policy --> PASS | WARN | REWRITE | BLOCK
    REWRITE --> rewrite --> re-score --> accept, or escalate to BLOCK
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import config
from .confidence import ConfidenceScorer
from .incorrectness import IncorrectnessScorer
from .knowledge import ReferenceIndex, Reference
from .policy import fsc as fsc_formula, decide
from .providers import get_provider, Provider
from . import rewrite as rw

BASELINE_SYSTEM = "You are a helpful assistant."


@dataclass
class Score:
    risk: object
    confidence: object
    incorrectness: object
    reference: Reference | None
    fsc: float

    def to_dict(self):
        return {
            "risk": self.risk.to_dict(),
            "confidence": self.confidence.to_dict(),
            "incorrectness": self.incorrectness.to_dict(),
            "reference": self.reference.to_dict() if self.reference else None,
            "fsc": round(self.fsc, 4),
        }


@dataclass
class GuardReport:
    question: str
    original_response: str
    final_response: str
    action: str
    reasons: list
    score: Score
    after: dict | None = None  # re-score of the remediated answer
    remediation: str | None = None  # "rule" | "llm" | "block" | None
    provider: str | None = None
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "question": self.question,
            "provider": self.provider,
            "action": self.action,
            "reasons": self.reasons,
            "original_response": self.original_response,
            "final_response": self.final_response,
            "remediation": self.remediation,
            **self.score.to_dict(),
            "after": self.after,
            "timings_ms": self.timings_ms,
        }


class Guard:
    def __init__(self, judge: str | None = None):
        from .risk import RiskClassifier
        self.risk = RiskClassifier()
        self.index = ReferenceIndex()
        self.conf = ConfidenceScorer()
        judge = judge if judge is not None else config.JUDGE
        judge_provider = get_provider(judge) if judge else None
        self.inc = IncorrectnessScorer(self.index, judge_provider)

    # ------------------------------------------------------------------ scoring
    def score(self, question: str, response: str, reference: Reference | None = None,
              tier: int | None = None, danger_text: str | None = None) -> Score:
        """Score one (question, response) pair.

        reference: pass a known ground truth (benchmark mode); otherwise it is retrieved.
        tier: force a tier (benchmark mode uses the annotated tier).
        danger_text: score danger/polarity on this text instead of the full response
                     (used so MedShield's own safety note can't attenuate the danger score).
        """
        risk = self.risk.classify(question)
        if tier is not None:
            risk.tier = tier
        if reference is None:
            ref = self.index.lookup(question)
            reference = ref if ref.similarity >= config.REF_MATCH_MIN else None
        conf = self.conf.score(response)
        body = danger_text if danger_text is not None else response
        inc = self.inc.score(question, body, risk.tier, reference)
        # missing-safety is judged on the full response (a safety note does count there)
        if danger_text is not None:
            ms_full = self.inc.missing_safety(response, risk.tier)
            inc.score = max(0.0, min(1.0, inc.score - 0.40 * (inc.missing_safety - ms_full)))
            inc.missing_safety = ms_full
        return Score(risk, conf, inc, reference, fsc_formula(conf.score, inc.score, risk.tier))

    # ------------------------------------------------------------------ guarding
    def analyze(self, question: str, response: str, rewriter: Provider | None = None,
                reference: Reference | None = None, tier: int | None = None) -> GuardReport:
        t0 = time.perf_counter()
        s = self.score(question, response, reference=reference, tier=tier)
        action, reasons = decide(s.fsc, s.risk, s.incorrectness)
        t1 = time.perf_counter()
        report = GuardReport(question, response, response, action, reasons, s,
                             timings_ms={"score": round((t1 - t0) * 1000, 1)})
        crisis = s.risk.crisis

        if action == "WARN":
            report.final_response = rw.join(response, rw.safety_note(s.risk.tier, crisis), lead=crisis)
            report.remediation = "note"
            after = self.score(question, report.final_response, reference=s.reference,
                               tier=s.risk.tier, danger_text=response)
            report.after = after.to_dict()

        elif action == "REWRITE":
            method = "llm" if rewriter is not None else "rule"
            try:
                body, note = (rw.llm_rewrite(rewriter, question, response, s.risk.tier, crisis)
                              if rewriter is not None else rw.rule_rewrite(response, s.risk.tier, crisis))
            except Exception as e:
                reasons.append(f"LLM rewrite failed ({e.__class__.__name__}); used rule rewrite")
                method = "rule"
                body, note = rw.rule_rewrite(response, s.risk.tier, crisis)
            final = rw.join(body, note, lead=crisis)
            after = self.score(question, final, reference=s.reference, tier=s.risk.tier, danger_text=body)
            report.after = after.to_dict()
            if after.fsc >= config.REWRITE_AT or after.incorrectness.danger >= 0.4:
                report.action = "BLOCK"
                reasons.append(f"rewrite did not bring FSC below {config.REWRITE_AT} "
                               f"(got {after.fsc:.2f}); escalated to BLOCK")
            else:
                report.final_response, report.remediation = final, method
                reasons.append(f"{method} rewrite verified: FSC {s.fsc:.2f} -> {after.fsc:.2f}")

        if report.action == "BLOCK":
            body, note = rw.block_message(s.risk.tier, crisis, s.reference, s.incorrectness.contraindication)
            report.final_response = rw.join(body, note, lead=crisis)
            report.remediation = "block"
            if report.after is None:
                after = self.score(question, report.final_response, reference=s.reference,
                                   tier=s.risk.tier, danger_text=body)
                report.after = after.to_dict()

        report.timings_ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
        return report

    def ask(self, question: str, provider: Provider, rewrite_with_llm: bool = True,
            history: list | None = None) -> GuardReport:
        t0 = time.perf_counter()
        messages = [{"role": "system", "content": BASELINE_SYSTEM}, *(history or []),
                    {"role": "user", "content": question}]
        response = provider.chat(messages)
        gen_ms = round((time.perf_counter() - t0) * 1000, 1)
        report = self.analyze(question, response, rewriter=provider if rewrite_with_llm else None)
        report.provider = provider.label
        report.timings_ms["generation"] = gen_ms
        return report
