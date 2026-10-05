"""Wires settings, services, the library, search, answers and quizzes together."""

from __future__ import annotations

from dataclasses import dataclass, field

from .answering import Answerer
from .config import Settings, load_settings
from .library import Library
from .quiz import QuizMaker
from .retrieval import Retriever
from .services import (
    ChatStyleImageEmbedder,
    HashingTextEmbedder,
    OpenAIChatModel,
    OpenAIReranker,
    OpenAITextEmbedder,
    OverlapReranker,
    PageReader,
    PageTextImageEmbedder,
)


@dataclass
class Services:
    chat_model: object | None
    text_embedder: object
    image_embedder: object
    reranker: object
    page_reader: object | None
    status: list[tuple[str, str, bool]] = field(default_factory=list)  # (role, description, is_real_service)


def build_services(settings: Settings) -> Services:
    """Use each class service that is configured; fall back to an offline stand-in otherwise."""
    timeout = settings.request_timeout
    status = []

    chat = OpenAIChatModel(settings.llm, timeout) if settings.llm.configured else None
    status.append(("Answers and quizzes", chat.name if chat else "not set: offline mode quotes passages instead of writing answers", bool(chat)))

    if settings.text_embed.configured:
        text_embedder = OpenAITextEmbedder(settings.text_embed, timeout)
    else:
        text_embedder = HashingTextEmbedder()
    status.append(("Text search by meaning", text_embedder.name, settings.text_embed.configured))

    if settings.visual_embed.configured:
        image_embedder = ChatStyleImageEmbedder(settings.visual_embed, timeout)
    else:
        image_embedder = PageTextImageEmbedder()
    status.append(("Image search", image_embedder.name, settings.visual_embed.configured))

    reranker = OpenAIReranker(settings.rerank, timeout) if settings.rerank.configured else OverlapReranker()
    status.append(("Reranking", reranker.name if settings.use_rerank else "turned off (USE_RERANK=false)", settings.rerank.configured))

    page_reader = PageReader(OpenAIChatModel(settings.parser, timeout)) if settings.parser.configured else None
    status.append(("Reading text inside images", page_reader.name if page_reader else "not set: image-only slides are found by image search only", bool(page_reader)))

    return Services(chat, text_embedder, image_embedder, reranker, page_reader, status)


class CourseAssistant:
    def __init__(self, settings: Settings | None = None, services: Services | None = None):
        self.settings = settings or load_settings()
        self.services = services or build_services(self.settings)
        self.library = Library(
            self.settings.data_dir,
            self.services.text_embedder,
            self.services.image_embedder,
            soffice=self.settings.soffice_path,
            page_reader=self.services.page_reader,
        )
        self.retriever = Retriever(
            self.library,
            self.services.reranker,
            mode=self.settings.retrieval_mode,
            use_rerank=self.settings.use_rerank,
        )
        self.answerer = Answerer(self.retriever, self.services.chat_model)
        self.quiz_maker = QuizMaker(self.library, self.retriever, self.services.chat_model)

    def status_markdown(self) -> str:
        lines = ["| Part | Using | |", "|---|---|---|"]
        for role, description, real in self.services.status:
            lines.append(f"| {role} | {description} | {'class service' if real else 'offline stand-in'} |")
        mode = "keyword + text + image search" if self.settings.retrieval_mode != "embeddings_only" else "text + image search (no keyword search)"
        lines.append(f"| Search mode | {mode} | |")
        return "\n".join(lines)
