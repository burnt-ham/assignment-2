"""Settings, read from environment variables or a local `.env` file.

Keys are only ever read here and passed to the service clients. They are never
printed, logged, or shown in the interface (see `redact`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ServiceConfig:
    """Connection details for one class service (OpenAI-compatible style)."""

    base_url: str = ""
    api_key: str = ""
    model: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


@dataclass
class Settings:
    llm: ServiceConfig = field(default_factory=ServiceConfig)
    text_embed: ServiceConfig = field(default_factory=ServiceConfig)
    visual_embed: ServiceConfig = field(default_factory=ServiceConfig)
    rerank: ServiceConfig = field(default_factory=ServiceConfig)
    parser: ServiceConfig = field(default_factory=ServiceConfig)

    data_dir: Path = PROJECT_ROOT / "data"
    # "hybrid" = keyword + text embeddings + visual embeddings
    # "embeddings_only" = text + visual embeddings, no keyword search
    retrieval_mode: str = "hybrid"
    use_rerank: bool = True
    request_timeout: float = 120.0
    soffice_path: str = "soffice"

    def secrets(self) -> list[str]:
        return [s.api_key for s in (self.llm, self.text_embed, self.visual_embed, self.rerank, self.parser) if s.api_key]


def _service(prefix: str, shared_key: str) -> ServiceConfig:
    return ServiceConfig(
        base_url=os.getenv(f"{prefix}_BASE_URL", "").strip().rstrip("/"),
        api_key=os.getenv(f"{prefix}_API_KEY", "").strip() or shared_key,
        model=os.getenv(f"{prefix}_MODEL", "").strip(),
    )


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Load settings from the environment, after reading `.env` if present."""
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    shared_key = os.getenv("CLASS_API_KEY", "").strip()
    data_dir = os.getenv("DATA_DIR", "").strip()
    return Settings(
        llm=_service("LLM", shared_key),
        text_embed=_service("TEXT_EMBED", shared_key),
        visual_embed=_service("VISUAL_EMBED", shared_key),
        rerank=_service("RERANK", shared_key),
        parser=_service("PARSER", shared_key),
        data_dir=Path(data_dir) if data_dir else PROJECT_ROOT / "data",
        retrieval_mode=os.getenv("RETRIEVAL_MODE", "hybrid").strip() or "hybrid",
        use_rerank=_bool("USE_RERANK", True),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "120")),
        soffice_path=os.getenv("SOFFICE_PATH", "soffice").strip() or "soffice",
    )


_BEARER = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


def redact(text: str, secrets: list[str] | None = None) -> str:
    """Remove keys from text before it is shown or logged."""
    text = _BEARER.sub(r"\1[hidden]", str(text))
    for secret in secrets or []:
        if secret:
            text = text.replace(secret, "[hidden]")
    return text
