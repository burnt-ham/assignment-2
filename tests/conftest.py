"""Shared test helpers: small sample files and fake AI services (no network needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from course_assistant.assistant import CourseAssistant, Services  # noqa: E402
from course_assistant.config import Settings  # noqa: E402
from course_assistant.services import (  # noqa: E402
    HashingTextEmbedder,
    OverlapReranker,
    PageTextImageEmbedder,
    ServiceError,
)


def make_pdf(path: Path, pages: list[str]) -> Path:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 750), text, fontsize=12)
    document.save(path)
    document.close()
    return path


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    return make_pdf(
        tmp_path / "rag-notes.pdf",
        [
            "Retrieval augmented generation\nRAG retrieves relevant information from documents and adds it to the model context.",
            "Chunking\nChunking divides parsed content into useful passages while preserving source metadata such as page numbers.",
            "Reranking\nReranking compares candidate chunks to the original question and prioritizes which chunks are supplied.",
        ],
    )


@pytest.fixture
def sample_md(tmp_path: Path) -> Path:
    path = tmp_path / "syllabus.md"
    path.write_text(
        "# Office hours\nOffice hours are Tuesdays and Fridays from 11am to 12pm via Zoom.\n\n"
        "# Grading\nAssignments are worth 20 percent and the final project is worth 45 percent.\n",
        encoding="utf-8",
    )
    return path


class FakeChat:
    """Returns scripted replies in order; can be told to fail like an unreachable service."""

    name = "fake chat model"

    def __init__(self, replies: list | None = None, fail: bool = False):
        self.replies = list(replies or [])
        self.fail = fail
        self.calls: list[dict] = []

    def complete(self, system, user, image_paths=None, max_tokens=2000):
        self.calls.append({"system": system, "user": user, "images": list(image_paths or [])})
        if self.fail:
            raise ServiceError("couldn't reach the chat model service at http://example.invalid")
        reply = self.replies.pop(0) if self.replies else "{}"
        return reply if isinstance(reply, str) else json.dumps(reply)


class FailingReranker:
    name = "failing reranker"

    def rerank(self, query, candidates):
        raise ServiceError("the reranker service didn't respond in time")


def offline_services(chat=None, reranker=None) -> Services:
    return Services(
        chat_model=chat,
        text_embedder=HashingTextEmbedder(),
        image_embedder=PageTextImageEmbedder(),
        reranker=reranker or OverlapReranker(),
        page_reader=None,
    )


@pytest.fixture
def make_assistant(tmp_path: Path):
    def factory(chat=None, reranker=None, **settings) -> CourseAssistant:
        config = Settings(data_dir=tmp_path / "data", **settings)
        return CourseAssistant(config, offline_services(chat, reranker))

    return factory
