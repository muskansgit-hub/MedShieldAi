"""
MedFSC-Bench runner: query real models, score with FSC, build the leaderboard.

    # 1. collect responses (resumable; re-run the same command to continue)
    python -m medshield.bench run --provider ollama --model llama3.2
    python -m medshield.bench run --provider groq --model llama-3.1-8b-instant --limit 120

    # 2. aggregate every scored run into results/leaderboard.json + .csv
    python -m medshield.bench report

Each run writes:
    results/runs/<provider>__<model>.jsonl     raw responses (cache)
    results/scored/<provider>__<model>.jsonl   per-response FSC before / after guard
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np

from .. import config
from ..guard import Guard, BASELINE_SYSTEM
from ..knowledge import Reference
from ..providers import get_provider, ProviderError
from ..risk import load_bench

RUNS = config.RESULTS_DIR / "runs"
SCORED = config.RESULTS_DIR / "scored"


def slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", label.replace("/", "__"))


def select(items, limit, tiers, seed):
    if tiers:
        items = [d for d in items if d["risk_tier"] in tiers]
    if limit and limit < len(items):
        # stratified by tier so a small run still covers every tier
        rng = random.Random(seed)
        by = defaultdict(list)
        for d in items:
            by[d["risk_tier"]].append(d)
        out = []
        for t, group in sorted(by.items()):
            k = max(1, round(limit * len(group) / len(items)))
            out += rng.sample(group, min(k, len(group)))
        items = sorted(out, key=lambda d: d["prompt_id"])[:limit]
    return items


def collect(args) -> str:
    prov = get_provider(args.provider, args.model)
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{slug(prov.label)}.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(l)["prompt_id"] for l in path.open(encoding="utf-8") if l.strip()}
    items = [d for d in select(load_bench(), args.limit, args.tiers, args.seed) if d["prompt_id"] not in done]
    print(f"[collect] {prov.label}: {len(done)} cached, {len(items)} to query -> {path}")

    def one(d):
        t0 = time.perf_counter()
        try:
            text, err = prov.chat([{"role": "system", "content": BASELINE_SYSTEM},
                                   {"role": "user", "content": d["prompt"]}],
                                  temperature=args.temperature, max_tokens=args.max_tokens), None
        except ProviderError as e:
            text, err = "", str(e)
        return {"prompt_id": d["prompt_id"], "risk_tier": d["risk_tier"], "type_label": d["type_label"],
                "prompt": d["prompt"], "ground_truth": d["ground_truth"], "source": d["source"],
                "model": prov.label, "response": text, "error": err,
                "latency_ms": round((time.perf_counter() - t0) * 1000), "temperature": args.temperature}

    errors = 0
    with path.open("a", encoding="utf-8") as f, ThreadPoolExecutor(args.concurrency) as ex:
        futs = [ex.submit(one, d) for d in items]
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            if rec["error"]:
                errors += 1
                if errors >= 5 and errors == i:
                    print(f"[collect] first {errors} requests all failed: {rec['error']}")
                    ex.shutdown(cancel_futures=True)
                    break
                continue  # failed calls are not cached, so a re-run retries them
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            if i % 10 == 0 or i == len(items):
                print(f"  {i}/{len(items)}  ({errors} errors)")
    return prov.label


def score_run(label: str, judge: str | None):
    g = Guard(judge=judge or "")
    src = RUNS / f"{slug(label)}.jsonl"
    rows = [json.loads(l) for l in src.open(encoding="utf-8") if l.strip()]
    SCORED.mkdir(parents=True, exist_ok=True)
    out = SCORED / f"{slug(label)}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            ref = Reference(r["prompt_id"], r["prompt"], r["ground_truth"], r["source"], 1.0)
            rep = g.analyze(r["prompt"], r["response"], reference=ref, tier=r["risk_tier"])
            d = rep.to_dict()
            f.write(json.dumps({
                "prompt_id": r["prompt_id"], "model": label, "risk_tier": r["risk_tier"],
                "type_label": r["type_label"], "C": d["confidence"]["score"],
                "I": d["incorrectness"]["score"], "fsc": d["fsc"],
                "fsc_delivered": (d["after"] or {}).get("fsc", d["fsc"]),
                "action": d["action"], "danger": d["incorrectness"]["danger"],
                "polarity_conflict": d["incorrectness"]["polarity_conflict"],
                "missing_safety": d["incorrectness"]["missing_safety"],
                "synthetic": label.startswith("mock/"),
            }) + "\n")
    print(f"[score] {label}: {len(rows)} responses -> {out}")


def report():
    from scipy.stats import spearmanr, kruskal
    models = []
    for p in sorted(SCORED.glob("*.jsonl")):
        rows = [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]
        if not rows:
            continue
        by_t = defaultdict(list)
        for r in rows:
            by_t[r["risk_tier"]].append(r)
        tiers = sorted(by_t)
        fsc = np.array([r["fsc"] for r in rows])
        deliv = np.array([r["fsc_delivered"] for r in rows])
        tier_arr = np.array([r["risk_tier"] for r in rows])
        rho, p_rho = spearmanr(tier_arr, fsc) if len(set(tier_arr)) > 1 else (None, None)
        groups = [fsc[tier_arr == t] for t in tiers]
        p_kw = kruskal(*groups).pvalue if len(groups) > 1 and fsc.std() > 0 else None
        adv = [r for r in rows if r["type_label"] == "Adversarially Framed"]
        t4 = by_t.get(4, [])
        mean = lambda xs: float(np.mean(xs)) if len(xs) else None
        models.append({
            "model": rows[0]["model"], "synthetic": rows[0]["synthetic"], "n": len(rows),
            "fsc_by_tier": {str(t): round(mean([r["fsc"] for r in by_t[t]]), 4) for t in tiers},
            "fsc_delivered_by_tier": {str(t): round(mean([r["fsc_delivered"] for r in by_t[t]]), 4) for t in tiers},
            "n_by_tier": {str(t): len(by_t[t]) for t in tiers},
            "avg_fsc": round(float(fsc.mean()), 4),
            "avg_fsc_delivered": round(float(deliv.mean()), 4),
            "fsc_reduction_pct": round(100 * (1 - deliv.mean() / fsc.mean()), 1) if fsc.mean() > 0 else 0.0,
            "mean_confidence": round(mean([r["C"] for r in rows]), 4),
            "mean_incorrectness": round(mean([r["I"] for r in rows]), 4),
            "actions": {a: sum(r["action"] == a for r in rows) for a in ("PASS", "WARN", "REWRITE", "BLOCK")},
            "adversarial_validation_rate": round(mean([r["polarity_conflict"] for r in adv]), 4) if adv else None,
            "tier4_unhedged_rate": round(mean([r["missing_safety"] > 0 for r in t4]), 4) if t4 else None,
            "h1_spearman_rho": None if rho is None else round(float(rho), 3),
            "h1_spearman_p": None if p_rho is None else float(f"{p_rho:.3g}"),
            "h1_kruskal_p": None if p_kw is None else float(f"{p_kw:.3g}"),
        })
    models.sort(key=lambda m: m["avg_fsc"])
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "formula": "FSC = C x I x W/4", "models": models}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "leaderboard.json").write_text(json.dumps(out, indent=2))
    with (config.RESULTS_DIR / "leaderboard.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "synthetic", "n", "T1", "T2", "T3", "T4", "avg_fsc", "avg_fsc_guarded",
                    "reduction_pct", "adversarial_validation_rate", "tier4_unhedged_rate", "h1_rho", "h1_p"])
        for m in models:
            t = m["fsc_by_tier"]
            w.writerow([m["model"], m["synthetic"], m["n"], *(t.get(str(i), "") for i in range(1, 5)),
                        m["avg_fsc"], m["avg_fsc_delivered"], m["fsc_reduction_pct"],
                        m["adversarial_validation_rate"], m["tier4_unhedged_rate"],
                        m["h1_spearman_rho"], m["h1_spearman_p"]])
    print(f"[report] {len(models)} model(s) -> {config.RESULTS_DIR / 'leaderboard.json'}")
    for m in models:
        tag = " (synthetic)" if m["synthetic"] else ""
        print(f"  {m['model']:<45}{tag} n={m['n']:<4} avg FSC {m['avg_fsc']:.3f} -> guarded "
              f"{m['avg_fsc_delivered']:.3f} ({m['fsc_reduction_pct']}% lower)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m medshield.bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="query a model on MedFSC-Bench, then score it")
    r.add_argument("--provider", required=True, help="ollama | openai | groq | nvidia | gemini | anthropic | openrouter | together | mock")
    r.add_argument("--model")
    r.add_argument("--limit", type=int, help="stratified sample size (default: all 487)")
    r.add_argument("--tiers", type=lambda s: [int(x) for x in s.split(",")])
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--temperature", type=float, default=0.7)
    r.add_argument("--max-tokens", type=int, default=512)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--judge", help="optional LLM judge spec for incorrectness, e.g. openai/gpt-4o-mini")
    s = sub.add_parser("score", help="re-score cached responses (e.g. after changing lexicons)")
    s.add_argument("label", help="provider/model as stored, e.g. ollama/llama3.2")
    s.add_argument("--judge")
    sub.add_parser("report", help="aggregate scored runs into the leaderboard")
    a = ap.parse_args(argv)

    if a.cmd == "run":
        try:
            label = collect(a)
        except ProviderError as e:
            sys.exit(f"provider error: {e}")
        score_run(label, a.judge)
        report()
    elif a.cmd == "score":
        score_run(a.label, a.judge)
        report()
    else:
        report()


if __name__ == "__main__":
    main()
