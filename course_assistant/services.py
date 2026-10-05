"""The AI services the app depends on, and offline stand-ins for each.

The app needs four kinds of service:
- a chat model that can read images (answers and quizzes)
- a text embedding model (search by meaning over text chunks)
- a visual embedding model (search over images of pages and slides)
- a reranker (re-orders the combined search results)

Each has a small interface here. The offline stand-ins need no network and no
key. They let the app and its tests run anywhere, but they are much weaker than
real models: the visual stand-in uses a page's text rather than its picture, so
it cannot find image-only slides.
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from PIL import Image

from .config import ServiceConfig, redact


class ServiceError(Exception):
    """A service failed or couldn't be reached. The message is safe to show."""


@dataclass
class Candidate:
    """A search result to rerank: a text chunk or a page image."""

    key: str
    kind: str  # "text" or "image"
    text: str = ""
    image_path: str = ""
    metadata: dict = field(default_factory=dict)


class TextEmbedder(Protocol):
    name: str

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class ImageEmbedder(Protocol):
    name: str

    def embed_images(self, image_paths: list[str], page_texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, candidates: list[Candidate]) -> list[float]: ...


class ChatModel(Protocol):
    name: str

    def complete(self, system: str, user: str, image_paths: list[str] | None = None, max_tokens: int = 2000) -> str: ...


# ---------------------------------------------------------------------------
# Offline stand-ins
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an and are as at be by can do for from has have how in is it its of on or that the this to was what when "
    "which who why with you your does did into than then them they their there these those about".split()
)


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _hash_vector(text: str, dims: int = 512) -> list[float]:
    vector = [0.0] * dims
    for word in tokens(text):
        bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % dims
        vector[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class HashingTextEmbedder:
    """Stand-in: word-count vectors. Matches shared words, not meaning."""

    name = "offline word-hashing (stand-in)"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(t) for t in texts]


class PageTextImageEmbedder:
    """Stand-in for visual embeddings: uses each page's text, not its picture."""

    name = "offline page-text stand-in (no real image understanding)"

    def embed_images(self, image_paths: list[str], page_texts: list[str]) -> list[list[float]]:
        return [_hash_vector(t) for t in page_texts]

    def embed_query(self, query: str) -> list[float]:
        return _hash_vector(query)


class OverlapReranker:
    """Stand-in reranker: share of query words that appear in the candidate."""

    name = "offline word-overlap (stand-in)"

    def rerank(self, query: str, candidates: list[Candidate]) -> list[float]:
        query_words = set(tokens(query))
        scores = []
        for candidate in candidates:
            words = set(tokens(candidate.text))
            scores.append(len(query_words & words) / (len(query_words) or 1))
        return scores


# ---------------------------------------------------------------------------
# Class services (OpenAI-compatible HTTP APIs, as served by vLLM)
# ---------------------------------------------------------------------------

MAX_IMAGE_SIDE = 1280


def image_data_url(path: str, max_side: int = MAX_IMAGE_SIDE) -> str:
    """Load an image, shrink it if large, and return it as a base64 data URL."""
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


class _HttpService:
    label = "service"

    def __init__(self, config: ServiceConfig, timeout: float = 120.0):
        self.config = config
        self.timeout = timeout
        self.name = f"{config.model} ({self.label})"

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.config.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise ServiceError(f"the {self.label} service at {self.config.base_url} didn't respond in time") from exc
        except httpx.HTTPError as exc:
            raise ServiceError(f"couldn't reach the {self.label} service at {self.config.base_url}") from exc
        if response.status_code >= 400:
            detail = redact(response.text[:300], [self.config.api_key])
            raise ServiceError(f"the {self.label} service returned an error ({response.status_code}): {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise ServiceError(f"the {self.label} service sent a reply that isn't JSON") from exc


class OpenAIChatModel(_HttpService):
    """Vision-capable chat model via /chat/completions."""

    label = "chat model"

    def complete(self, system: str, user: str, image_paths: list[str] | None = None, max_tokens: int = 2000) -> str:
        content: list[dict] = [{"type": "text", "text": user}]
        for path in image_paths or []:
            content.append({"type": "image_url", "image_url": {"url": image_data_url(path)}})
        payload = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        data = self._post("/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceError("the chat model's reply was missing its message") from exc


class OpenAITextEmbedder(_HttpService):
    """Text embeddings via /embeddings."""

    label = "text embedding"
    batch_size = 32

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            data = self._post("/embeddings", {"model": self.config.model, "input": batch, "encoding_format": "float"})
            try:
                rows = sorted(data["data"], key=lambda row: row["index"])
                vectors.extend(row["embedding"] for row in rows)
            except (KeyError, TypeError) as exc:
                raise ServiceError("the text embedding reply was missing its vectors") from exc
        return vectors


class ChatStyleImageEmbedder(_HttpService):
    """Visual embeddings via /embeddings with chat-style messages (vLLM multimodal embedding format).

    Images and text queries are embedded into the same space, so a text
    question can find a matching slide image.
    """

    label = "visual embedding"

    def _embed(self, content: list[dict]) -> list[float]:
        payload = {"model": self.config.model, "messages": [{"role": "user", "content": content}], "encoding_format": "float"}
        data = self._post("/embeddings", payload)
        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceError("the visual embedding reply was missing its vector") from exc

    def embed_images(self, image_paths: list[str], page_texts: list[str]) -> list[list[float]]:
        return [self._embed([{"type": "image_url", "image_url": {"url": image_data_url(p)}}]) for p in image_paths]

    def embed_query(self, query: str) -> list[float]:
        return self._embed([{"type": "text", "text": query}])


class OpenAIReranker(_HttpService):
    """Multimodal reranking via /rerank (Jina/Cohere-style format served by vLLM).

    Text and image candidates are scored in separate calls, since some servers
    don't accept mixed lists.
    """

    label = "reranker"

    def _score(self, query: str, documents: list) -> list[float]:
        data = self._post("/rerank", {"model": self.config.model, "query": query, "documents": documents, "top_n": len(documents)})
        scores = [0.0] * len(documents)
        try:
            for row in data["results"]:
                scores[row["index"]] = float(row["relevance_score"])
        except (KeyError, TypeError, IndexError) as exc:
            raise ServiceError("the reranker reply was missing its scores") from exc
        return scores

    def rerank(self, query: str, candidates: list[Candidate]) -> list[float]:
        scores = [0.0] * len(candidates)
        texts = [i for i, c in enumerate(candidates) if c.kind == "text"]
        images = [i for i, c in enumerate(candidates) if c.kind == "image"]
        if texts:
            for i, score in zip(texts, self._score(query, [candidates[i].text for i in texts])):
                scores[i] = score
        if images:
            documents = [{"content": [{"type": "image_url", "image_url": {"url": image_data_url(candidates[i].image_path)}}]} for i in images]
            for i, score in zip(images, self._score(query, documents)):
                scores[i] = score
        return scores


OCR_PROMPT = "Read this page image. Return all visible text, including text inside pictures, charts and memes, as plain text. Return nothing else."


class PageReader:
    """Document parsing service: reads text from page images (e.g. slides that are mostly pictures)."""

    def __init__(self, chat_model: ChatModel):
        self.chat_model = chat_model
        self.name = chat_model.name

    def read(self, image_path: str) -> str:
        return self.chat_model.complete("You transcribe documents accurately.", OCR_PROMPT, [image_path], max_tokens=1500).strip()
