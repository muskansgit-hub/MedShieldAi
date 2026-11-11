"""
Remediation: turn an over-confident answer into a calibrated one.

Two strategies:
  rule  deterministic: soften absolute phrases + tier-specific safety note
  llm   ask an LLM to rewrite with calibrated uncertainty, keeping correct facts

Either way the guard re-scores the result and escalates to BLOCK if FSC is still
too high, so a rewrite is only accepted once it has been verified.
"""
from __future__ import annotations

import re

from . import config
from .lexicons import SOFTEN_MAP

REWRITE_SYSTEM = """You are a medical safety editor. Rewrite the assistant answer below so its \
confidence matches the evidence:
- keep information that is correct and well established; remove or correct anything unsafe
- never state that something is completely safe or risk-free
- say clearly what depends on the person's situation
- for medication, dosing, pregnancy, children or worrying symptoms, advise checking with a \
doctor or pharmacist; for emergency symptoms, tell them to call emergency services now
- do not add new medical claims you are not sure of
- keep it concise and plain-language
Return only the rewritten answer."""


def safety_note(tier: int, crisis: bool = False) -> str:
    notes = []
    if crisis:
        notes.append(
            f"If you are thinking about harming yourself, please reach out right now: "
            f"{config.CRISIS_LINE}, or call {config.EMERGENCY_NUMBER}. You don't have to face this alone."
        )
        return notes[0]
    if tier == 2:
        notes.append("This is general information, not a diagnosis. If symptoms persist, get worse "
                     "or you are unsure, speak to a pharmacist or GP.")
    elif tier == 3:
        notes.append("Doses, drug interactions and changes to medication depend on your health history. "
                     "Please confirm with a doctor or pharmacist before acting on this.")
    elif tier == 4:
        notes.append("This can be serious. Please check with a doctor, midwife or pharmacist before "
                     f"acting, and if symptoms are severe or sudden call {config.EMERGENCY_NUMBER} now.")
    return " ".join(notes)


def _soften(text: str) -> str:
    out = text
    for pat, repl in SOFTEN_MAP:
        def sub(m, repl=repl):
            if repl and m.group(0)[:1].isupper():
                return repl[:1].upper() + repl[1:]
            return repl
        out = re.sub(pat, sub, out, flags=re.I)
    out = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return re.sub(r"\s{2,}", " ", out).strip()


def rule_rewrite(response: str, tier: int, crisis: bool) -> tuple[str, str]:
    """Returns (body, note); final text is body + note."""
    return _soften(response), safety_note(tier, crisis)


def llm_rewrite(provider, question: str, response: str, tier: int, crisis: bool) -> tuple[str, str]:
    body = provider.chat(
        [
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": f"QUESTION: {question}\n\nANSWER TO REWRITE:\n{response}"},
        ],
        temperature=0.2, max_tokens=500,
    ).strip()
    return body, safety_note(tier, crisis) if crisis else ""


def block_message(tier: int, crisis: bool, reference=None, contraindication=None) -> tuple[str, str]:
    body = ("MedShield withheld the model's answer: it gave confident advice on a "
            "higher-risk medical question and may be unsafe.")
    if contraindication:
        body += f"\n\nKnown safety issue: {contraindication['guidance']}"
    elif reference is not None and reference.similarity >= 0.6:
        body += (f"\n\nClosest verified guidance ({reference.source}), for the question "
                 f"\"{reference.question}\": {reference.ground_truth}")
    return body, safety_note(max(tier, 2), crisis)


def join(body: str, note: str, lead: bool = False) -> str:
    """Attach the note after the body, or before it (lead=True, used for crisis resources)."""
    if not note:
        return body
    return f"{note}\n\n{body}" if lead else f"{body}\n\n{note}"
