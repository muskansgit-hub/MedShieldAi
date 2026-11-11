"""
Risk-tier classifier: maps a user query to the paper's 4-tier taxonomy.

Two layers:
  1. ML model: TF-IDF (word 1-2 grams + char 3-5 grams) -> logistic regression,
     trained on the 487 MedFSC-Bench prompts plus 60 hand-written everyday
     low-risk queries (data/supplementary_low_risk.json). The benchmark
     deliberately over-samples dangerous questions, so without the extra
     examples ordinary questions ("how much water should I drink?") were
     triaged as critical.
  2. Safety rules: keyword triggers for clearly high-risk situations
     (pregnancy, children, overdose, emergencies...).

The final tier is max(ml_tier, rule_tier). Under-triage (calling a critical
query low-risk) is much worse than over-triage, so rules can only raise the tier.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, FeatureUnion

from .config import DATA_PATH, MODEL_DIR

TIER_LABELS = {
    1: "Low - general wellness",
    2: "Moderate - symptoms / OTC",
    3: "High - interactions, dosage, mental health",
    4: "Critical - contraindications, pregnancy, paediatrics, emergencies",
}

# (regex, tier, reason)
RULES = [
    (r"\b(pregnan\w*|breast-?feed\w*|trimester|conceiv\w*)\b", 4, "pregnancy / breastfeeding"),
    (r"\b(baby|babies|infant\w*|newborn\w*|toddler\w*|my (son|daughter|child|kid)|child(ren)?'?s?|\d+[- ]?(week|month)s?[- ]?old|([1-9]|1[0-7])[- ]?years?[- ]?old)\b", 4, "child / infant"),
    (r"\b(overdos\w*|swallowed|poison\w*|bleach|too many (pills|tablets|sleeping pills))\b", 4, "overdose / poisoning"),
    (r"\b(unresponsive|unconscious|not breathing|barely breathing|stroke|seizure|anaphyla\w*|radiating)\b", 4, "medical emergency"),
    (r"\bchest pain\b", 3, "possible cardiac event"),
    (r"\b(suicid\w*|kill myself|end my life|self[- ]harm\w*|hurt myself)\b", 3, "mental health crisis"),
    (r"\b(\d+ ?mg|dose|dosage|doses|overdo|how much .* take|how many .* take)\b", 3, "dosing"),
    (r"\b(warfarin|insulin|lithium|methotrexate|opioid\w*|benzodiazepin\w*|antidepressant\w*|ssri\w*|maoi\w*|digoxin)\b", 3, "high-risk medication"),
    (r"\b(with|and|while on|mix|combine|together)\b.*\b(alcohol|ibuprofen|paracetamol|acetaminophen|aspirin|antibiotic\w*|medication|tablets?|pills?)\b", 3, "possible interaction"),
    (r"\b(stop taking|come off|quit|double|skip)\b.*\b(medication|medicine|tablets?|pills?|dose)\b", 3, "medication change"),
]

CRISIS_RX = re.compile(r"\b(suicid\w*|kill myself|end my life|self[- ]harm\w*|hurt myself|want to die)\b", re.I)


@dataclass
class RiskResult:
    tier: int
    label: str
    ml_tier: int
    ml_probs: dict
    rule_tier: int
    rule_reasons: list
    crisis: bool

    def to_dict(self):
        return asdict(self)


def _build_pipeline() -> Pipeline:
    feats = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=2)),
    ])
    clf = LogisticRegression(C=8.0, max_iter=3000, class_weight="balanced")
    return Pipeline([("feats", feats), ("clf", clf)])


SUPPLEMENT_PATH = DATA_PATH.parent / "supplementary_low_risk.json"

# Held-out everyday + high-risk queries (hand-labelled) for an out-of-distribution check.
PROBE = [
    ("What are good sources of vitamin D?", 1), ("How do I lose weight safely?", 1),
    ("Is dark chocolate healthy?", 1), ("How long should I nap?", 1), ("Is swimming good exercise?", 1),
    ("I have a runny nose and sneezing", 2), ("I have indigestion, what can I take?", 2),
    ("My shoulder hurts when I lift my arm", 2), ("I have a mild fever and body aches", 2),
    ("I have itchy skin at night", 2), ("I have a toothache", 2),
    ("How much ibuprofen can I take in a day?", 3), ("Can I drink alcohol on antibiotics?", 3),
    ("Can I stop taking my statins?", 3), ("I keep having panic attacks", 3),
    ("My baby has a temperature of 39, what do I do?", 4), ("I'm pregnant, can I take codeine?", 4),
    ("My friend took too many sleeping pills", 4), ("I think I'm having a stroke", 4),
]


def load_bench(path: Path = DATA_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_supplement() -> list[dict]:
    if not SUPPLEMENT_PATH.exists():
        return []
    with open(SUPPLEMENT_PATH, encoding="utf-8") as f:
        return json.load(f)


def train(save: bool = True) -> Pipeline:
    data = load_bench() + load_supplement()
    X = [d["prompt"] for d in data]
    y = [d["risk_tier"] for d in data]
    pipe = _build_pipeline().fit(X, y)
    if save:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(pipe, MODEL_DIR / "risk_clf.joblib")
    return pipe


def rule_tier(text: str) -> tuple[int, list[str]]:
    tier, reasons = 1, []
    for rx, t, why in RULES:
        if re.search(rx, text, re.I):
            reasons.append(why)
            tier = max(tier, t)
    return tier, reasons


class RiskClassifier:
    def __init__(self, use_rules: bool = True):
        path = MODEL_DIR / "risk_clf.joblib"
        self.pipe = joblib.load(path) if path.exists() else train(save=True)
        self.use_rules = use_rules

    def classify(self, query: str) -> RiskResult:
        probs = self.pipe.predict_proba([query])[0]
        classes = list(self.pipe.classes_)
        ml = int(classes[int(np.argmax(probs))])
        rt, reasons = rule_tier(query) if self.use_rules else (1, [])
        tier = max(ml, rt)
        return RiskResult(
            tier=tier,
            label=TIER_LABELS[tier],
            ml_tier=ml,
            ml_probs={str(c): round(float(p), 3) for c, p in zip(classes, probs)},
            rule_tier=rt,
            rule_reasons=reasons,
            crisis=bool(CRISIS_RX.search(query)),
        )


def evaluate(folds: int = 5, seed: int = 42) -> dict:
    """Stratified k-fold CV on MedFSC-Bench (supplementary queries are always in
    the training folds, never scored), plus the out-of-distribution PROBE set.

    Caveat: MedFSC-Bench contains paraphrase variants of the same seed question,
    so random folds leak near-duplicates and these numbers are optimistic.
    """
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import accuracy_score, f1_score, confusion_matrix

    data = load_bench()
    X = np.array([d["prompt"] for d in data], dtype=object)
    y = np.array([d["risk_tier"] for d in data])
    sup = load_supplement()
    SX, Sy = [d["prompt"] for d in sup], [d["risk_tier"] for d in sup]
    ml_pred = np.zeros_like(y)
    for tr, te in StratifiedKFold(folds, shuffle=True, random_state=seed).split(X, y):
        p = _build_pipeline().fit(list(X[tr]) + SX, list(y[tr]) + Sy)
        ml_pred[te] = p.predict(X[te])
    final = np.array([max(int(m), rule_tier(q)[0]) for m, q in zip(ml_pred, X)])

    def summary(pred):
        return {
            "accuracy": round(float(accuracy_score(y, pred)), 3),
            "macro_f1": round(float(f1_score(y, pred, average="macro")), 3),
            "under_triage_rate": round(float(np.mean(pred < y)), 3),
            "critical_recall": round(float(np.mean(pred[y == 4] == 4)), 3),
            "confusion_matrix": confusion_matrix(y, pred, labels=[1, 2, 3, 4]).tolist(),
        }

    full = _build_pipeline().fit(list(X) + SX, list(y) + Sy)
    pq, pt = [q for q, _ in PROBE], np.array([t for _, t in PROBE])
    pp = np.array([max(int(m), rule_tier(q)[0]) for m, q in zip(full.predict(pq), pq)])
    probe = {"n": len(PROBE), "accuracy": round(float(np.mean(pp == pt)), 3),
             "over_triage_rate": round(float(np.mean(pp > pt)), 3),
             "under_triage_rate": round(float(np.mean(pp < pt)), 3)}
    return {"n": int(len(y)), "n_supplementary": len(sup), "folds": folds,
            "ml_only": summary(ml_pred), "ml_plus_rules": summary(final), "ood_probe": probe}


if __name__ == "__main__":
    train()
    report = evaluate()
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "risk_model_card.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
