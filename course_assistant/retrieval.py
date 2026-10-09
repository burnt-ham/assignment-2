"""Hybrid retrieval: keyword + text-embedding + visual-embedding search, fused and reranked."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .library import Library
from .services import Candidate, Reranker, ServiceError

log = logging.getLogger(__name__)

RRF_K = 60  # standard constant for reciprocal rank fusion


@dataclass
class Evidence:
    """One piece of evidence given to the model, with everything needed to cite it."""

    evidence_id: str  # "E1", "E2", ...
    kind: str  # "text" (a chunk) or "image" (a whole page or slide image)
    doc_id: str
    doc_name: str
    page: int
    label: str
    title: str
    text: str
    image_path: str
    score: float

    @property
    def location(self) -> str:
        return f"{self.label} {self.page}"

    @property
    def citation(self) -> str:
        return f"{self.doc_name}, {self.location}"


@dataclass
class RetrievalResult:
    evidence: list[Evidence]
    warnings: list[str]
    reranked: bool


def reciprocal_rank_fusion(ranked_lists: list[list[str]]) -> dict[str, float]:
    """Combine several ranked lists into one score per item (higher is better)."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


class Retriever:
    def __init__(
        self,
        library: Library,
        reranker: Reranker | None,
        mode: str = "hybrid",
        use_rerank: bool = True,
        candidates_per_index: int = 20,
        max_text: int = 6,
        max_images: int = 3,
    ):
        self.library = library
        self.reranker = reranker
        self.mode = mode
        self.use_rerank = use_rerank
        self.candidates_per_index = candidates_per_index
        self.max_text = max_text
        self.max_images = max_images

    def search(self, query: str, doc_ids: list[str] | None = None, use_rerank: bool | None = None) -> RetrievalResult:
        warnings: list[str] = []
        k = self.candidates_per_index
        candidates: dict[str, Candidate] = {}
        ranked_lists: list[list[str]] = []
        # Per-call override; None means "use this retriever's stored default" so
        # the Ask tab can toggle without mutating shared state for the Quiz tab.
        rerank_requested = self.use_rerank if use_rerank is None else use_rerank

        def add_text_hits(hits):
            keys = []
            for chunk, _score in hits:
                key = f"text:{chunk.chunk_id}"
                candidates.setdefault(
                    key,
                    Candidate(key=key, kind="text", text=chunk.text, image_path=chunk.image_path, metadata={"chunk": chunk}),
                )
                keys.append(key)
            ranked_lists.append(keys)

        if self.mode != "embeddings_only":
            add_text_hits(self.library.keyword_search(query, k, doc_ids))
        try:
            add_text_hits(self.library.text_vector_search(query, k, doc_ids))
        except ServiceError as exc:
            warnings.append(f"Text search by meaning was skipped: {exc}")
        try:
            keys = []
            for doc_id, page, _score in self.library.image_search(query, k, doc_ids):
                key = f"image:{doc_id}:{page.number}"
                candidates.setdefault(
                    key,
                    Candidate(key=key, kind="image", text=page.text, image_path=page.image_path, metadata={"doc_id": doc_id, "page": page}),
                )
                keys.append(key)
            ranked_lists.append(keys)
        except ServiceError as exc:
            warnings.append(f"Image search was skipped: {exc}")

        fused = reciprocal_rank_fusion(ranked_lists)
        order = sorted(candidates.values(), key=lambda c: fused.get(c.key, 0.0), reverse=True)
        scores = {c.key: fused.get(c.key, 0.0) for c in order}

        reranked = False
        if rerank_requested and self.reranker is not None and order:
            try:
                rerank_scores = self.reranker.rerank(query, order)
                scores = {c.key: s for c, s in zip(order, rerank_scores)}
                order = sorted(order, key=lambda c: scores[c.key], reverse=True)
                reranked = True
            except ServiceError as exc:
                warnings.append(f"Reranking was skipped: {exc}")

        evidence: list[Evidence] = []
        texts = images = 0
        for candidate in order:
            if candidate.kind == "text" and texts < self.max_text:
                chunk = candidate.metadata["chunk"]
                texts += 1
                evidence.append(
                    Evidence("", "text", chunk.doc_id, chunk.doc_name, chunk.page, chunk.label, chunk.title, chunk.text, chunk.image_path, scores[candidate.key])
                )
            elif candidate.kind == "image" and images < self.max_images:
                page = candidate.metadata["page"]
                doc = self.library.documents.get(candidate.metadata["doc_id"])
                if doc is None:
                    continue
                images += 1
                evidence.append(
                    Evidence("", "image", doc.doc_id, doc.name, page.number, page.label, page.title, page.text, page.image_path, scores[candidate.key])
                )
        for index, item in enumerate(evidence, start=1):
            item.evidence_id = f"E{index}"
        return RetrievalResult(evidence=evidence, warnings=warnings, reranked=reranked)
