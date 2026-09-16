"""Retrieval boundary.

Question → retrieval → ranked `Evidence` → assistant. Two strategies exist today:

* `FullContextRetriever`: all servable entries. Correct while a hotel's content fits comfortably
  in the prompt (the demo hotel is about 2.8k tokens), and it can never miss the relevant entry.
* `KeywordRetriever`: keyword scoring, used by the offline engine.

A semantic retriever (embeddings + vector index) would implement `Retriever` too. It isn't built:
current content sizes don't need it, and adding it would introduce a way to miss evidence.
"""

from dataclasses import dataclass, field
import re
import time
from typing import Protocol

from .models import KnowledgeBase, KnowledgeEntry


@dataclass(frozen=True)
class Evidence:
    id: str
    title: str
    content: str
    score: float
    version: int
    source: str


@dataclass
class RetrievalResult:
    strategy: str
    evidence: list[Evidence] = field(default_factory=list)
    latency_ms: float = 0.0

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.evidence]


class Retriever(Protocol):
    def retrieve(self, kb: KnowledgeBase, query: str, limit: int | None = None) -> RetrievalResult: ...


def _evidence(entry: KnowledgeEntry, score: float) -> Evidence:
    return Evidence(entry.id, entry.title, entry.content, score, entry.version, entry.source)


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.lower()).strip()


class FullContextRetriever:
    name = "full_context"

    def retrieve(self, kb: KnowledgeBase, query: str, limit: int | None = None) -> RetrievalResult:
        started = time.perf_counter()
        evidence = [_evidence(e, 1.0) for e in kb.entries][:limit]
        return RetrievalResult(self.name, evidence, (time.perf_counter() - started) * 1000)


def keyword_score(entry: KnowledgeEntry, normalized_query: str) -> int:
    # Room entries share generic keywords ("room", "price") and would match almost everything,
    # so they only match by their specific room name.
    if entry.topic == "rooms":
        return 3 if entry.title.lower() in normalized_query else 0
    score = 0
    for kw in entry.keywords:
        if re.search(r"(?<![a-z])" + re.escape(kw.lower()) + r"(?![a-z])", normalized_query):
            score += 2 if " " in kw or "-" in kw else 1  # phrases are stronger signals
    return score


class KeywordRetriever:
    name = "keyword"

    def retrieve(self, kb: KnowledgeBase, query: str, limit: int | None = None) -> RetrievalResult:
        started = time.perf_counter()
        normalized = normalize_query(query)
        scored = [(keyword_score(e, normalized), e) for e in kb.entries]
        scored = sorted((s for s in scored if s[0] > 0), key=lambda s: -s[0])
        evidence = [_evidence(e, float(score)) for score, e in scored][:limit]
        return RetrievalResult(self.name, evidence, (time.perf_counter() - started) * 1000)
