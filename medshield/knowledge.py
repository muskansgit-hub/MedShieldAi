"""
Reference retriever: finds the closest verified MedFSC-Bench question for a
user query, so its NHS / WHO / Mayo ground truth can be used as a reference
when judging whether a response is wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

from .risk import load_bench


@dataclass
class Reference:
    prompt_id: str
    question: str
    ground_truth: str
    source: str
    similarity: float

    def to_dict(self):
        d = asdict(self)
        d["similarity"] = round(self.similarity, 3)
        return d


class ReferenceIndex:
    def __init__(self):
        self.items = load_bench()
        self.vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english")
        corpus = [d["prompt"] for d in self.items] + [d["ground_truth"] for d in self.items]
        self.vec.fit(corpus)
        # char n-grams make retrieval more robust to paraphrase and spelling
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.char.fit([d["prompt"] for d in self.items])
        self.q_matrix = self._embed([d["prompt"] for d in self.items])

    def _embed(self, texts):
        return normalize(hstack([self.vec.transform(texts), self.char.transform(texts)]).tocsr())

    def lookup(self, query: str) -> Reference:
        sims = cosine_similarity(self._embed([query]), self.q_matrix)[0]
        i = int(sims.argmax())
        d = self.items[i]
        return Reference(d["prompt_id"], d["prompt"], d["ground_truth"], d["source"], float(sims[i]))

    def text_distance(self, a: str, b: str) -> float:
        """1 - TF-IDF cosine similarity. Crude, but cheap and transparent."""
        m = self.vec.transform([a, b])
        return float(max(0.0, 1.0 - cosine_similarity(m[0], m[1])[0, 0]))


_YES = re.compile(r"^\W*(yes|yeah|sure|absolutely|definitely|of course|it is (perfectly |completely )?(safe|fine|okay|ok))\b", re.I)
_NO = re.compile(r"^\W*(no|not|never|do not|don't|you should not|you shouldn't|it is not|it isn't|avoid)\b", re.I)


def polarity(text: str) -> int:
    """+1 if the text opens with an affirmative answer, -1 if negative, 0 otherwise."""
    head = (text or "").strip()[:80]
    if _NO.search(head):
        return -1
    if _YES.search(head):
        return 1
    return 0
