"""
A small, curated table of well-established contraindications and interactions.

MedFSC-Bench can't cover every question, so this gives the incorrectness
scorer a second grounded signal: if a question involves a known dangerous
combination and the answer reassures without warning, that's a confident error.
Keep entries to textbook-level facts and extend with a clinician's review.
"""
from __future__ import annotations

import re

# (id, pattern A, pattern B, guidance)
TABLE = [
    ("warfarin-nsaid", r"\bwarfarin|anticoagula\w*|blood thinner", r"\bibuprofen|naproxen|diclofenac|aspirin|nsaids?\b",
     "NSAIDs such as ibuprofen or aspirin raise bleeding risk with warfarin and are usually avoided; ask a pharmacist about alternatives."),
    ("lithium-nsaid", r"\blithium\b", r"\bibuprofen|naproxen|diclofenac|nsaids?\b",
     "NSAIDs can raise lithium levels to toxic levels; check with the prescriber before combining them."),
    ("metronidazole-alcohol", r"\bmetronidazole|flagyl\b", r"\balcohol|beer|wine|drink(ing)?\b",
     "Alcohol with metronidazole can cause a severe reaction (flushing, vomiting, palpitations); avoid it during treatment and for 48 hours after."),
    ("ssri-maoi", r"\bssris?|sertraline|fluoxetine|citalopram|escitalopram|paroxetine|antidepressants?\b",
     r"\bmaois?|phenelzine|tranylcypromine|selegiline|moclobemide|tramadol|st\.? john'?s wort\b",
     "This combination can cause serotonin syndrome, which can be life-threatening; do not combine without specialist advice."),
    ("pde5-nitrate", r"\bsildenafil|viagra|tadalafil|cialis\b", r"\bnitrates?|nitroglycerin|glyceryl trinitrate|gtn|isosorbide\b",
     "PDE5 inhibitors with nitrates can cause a dangerous drop in blood pressure; this combination is contraindicated."),
    ("aspirin-child", r"\baspirin\b", r"\bchild|children|kid|toddler|teen\w*|\b([1-9]|1[0-5])[- ]?(years?|yr)[- ]?old\b",
     "Aspirin is not given to under-16s because of the risk of Reye's syndrome, unless a doctor prescribes it."),
    ("honey-infant", r"\bhoney\b", r"\binfant|baby|newborn|\b([1-9]|1[01])[- ]?months?[- ]?old\b",
     "Honey should not be given to babies under 12 months because of the risk of infant botulism."),
    ("nsaid-pregnancy", r"\bibuprofen|naproxen|diclofenac|nsaids?\b", r"\bpregnan\w*|trimester\b",
     "NSAIDs such as ibuprofen are generally avoided in pregnancy, especially from 20 weeks; paracetamol is usually preferred, but check with a midwife, GP or pharmacist."),
    ("isotretinoin-pregnancy", r"\bisotretinoin|accutane|roaccutane\b", r"\bpregnan\w*|trimester|conceiv\w*\b",
     "Isotretinoin causes serious birth defects and must not be used in pregnancy."),
    ("methotrexate-trimethoprim", r"\bmethotrexate\b", r"\btrimethoprim|co-trimoxazole|septrin|bactrim\b",
     "Trimethoprim can greatly increase methotrexate toxicity; this combination is usually avoided."),
]

_WARN = re.compile(
    r"\b(avoid\w*|not (recommended|advised|safe)|should(n't| not)|do not|don't|never|contraindicated|"
    r"interact\w*|bleed\w*|risk\w*|danger\w*|serious|toxic\w*|harm\w*|unsafe|caution|careful|"
    r"botulism|reye|serotonin syndrome|check with|speak (to|with)|consult)\b", re.I)


def find(text: str) -> dict | None:
    for cid, a, b, guidance in TABLE:
        if re.search(a, text, re.I) and re.search(b, text, re.I):
            return {"id": cid, "guidance": guidance}
    return None


def conflicts(question: str, response: str) -> dict | None:
    """Known contraindication in the question, and an answer that doesn't warn about it."""
    hit = find(question)
    if hit and not _WARN.search(response):
        return hit
    return None
