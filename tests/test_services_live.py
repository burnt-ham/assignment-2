"""Integration tests against the real class services (ports 9002/9003/9004).

These confirm the wire formats their client classes assume — the formats were
the team's open question (see README "Class service formats are unconfirmed").
They need `.env` / environment variables pointing at the live services:

    CLASS_API_KEY, TEXT_EMBED_BASE_URL, VISUAL_EMBED_BASE_URL, RERANK_BASE_URL

Without them the whole module is skipped, so ordinary (offline) test runs
stay green.
"""

import io
import os

import pytest
from dotenv import load_dotenv
from PIL import Image

from course_assistant.config import ServiceConfig
from course_assistant.services import (
    Candidate,
    ChatStyleImageEmbedder,
    OpenAIReranker,
    OpenAITextEmbedder,
)

# Read .env values WITHOUT leaking them into the test session: snapshot the
# environment, load the repo .env, capture just what we need, then restore.
# (A bare module-level load_dotenv() poisons other tests that assume a clean
# environment, e.g. test_config's load_settings assertions.)
_ENV_BEFORE = set(os.environ)
load_dotenv()
_REQUIRED_ENV = (
    "CLASS_API_KEY",
    "TEXT_EMBED_BASE_URL", "TEXT_EMBED_MODEL",
    "VISUAL_EMBED_BASE_URL", "VISUAL_EMBED_MODEL",
    "RERANK_BASE_URL", "RERANK_MODEL",
)
_ENV = {k: os.getenv(k) for k in _REQUIRED_ENV}
for _k in set(os.environ) - _ENV_BEFORE:
    os.environ.pop(_k, None)
del _ENV_BEFORE

pytestmark = pytest.mark.skipif(
    not all(_ENV.values()),
    reason="live class services not configured (CLASS_API_KEY / *_BASE_URL / *_MODEL)",
)


def cfg(env_prefix: str) -> ServiceConfig:
    return ServiceConfig(
        base_url=_ENV.get(f"{env_prefix}_BASE_URL", "").rstrip("/"),
        api_key=_ENV.get("CLASS_API_KEY", ""),
        model=_ENV.get(f"{env_prefix}_MODEL", ""),
    )


def make_image_bytes(draw_box: bool = True) -> bytes:
    image = Image.new("RGB", (64, 48), "white")
    if draw_box:
        for y in range(10, 40):
            for x in range(10, 56):
                image.putpixel((x, y), (0, 0, 128))  # navy box
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def test_text_embedding_format_matches_expected_shape(tmp_path):
    vectors = OpenAITextEmbedder(cfg("TEXT_EMBED")).embed_texts(
        ["a navy box drawn on a slide", "quantization makes models smaller"]
    )
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]) > 0


def test_visual_embedding_format_matches_text_space(tmp_path):
    image_path = tmp_path / "box.jpg"
    image_path.write_bytes(make_image_bytes())
    embedder = ChatStyleImageEmbedder(cfg("VISUAL_EMBED"))
    query = embedder.embed_query("a navy box")
    image = embedder.embed_images([str(image_path)], [""])[0]
    assert len(query) == len(image) > 0  # same space, so text can find the image


def test_reranker_scores_text_and_image_candidates_in_order(tmp_path):
    image_path = tmp_path / "box.jpg"
    image_path.write_bytes(make_image_bytes())
    candidates = [
        Candidate("t-navy", "text", text="a navy box drawn on a slide"),
        Candidate("t-unrelated", "text", text="semantic search uses embeddings"),
        Candidate("i-box", "image", image_path=str(image_path)),
    ]
    scores = OpenAIReranker(cfg("RERANK")).rerank("find the navy box", candidates)
    assert len(scores) == len(candidates)
    assert all(isinstance(s, float) for s in scores)
    # the clearly-matching text candidate should outrank the unrelated one
    assert scores[0] > scores[1]
    # and the picture of the navy box should also beat the unrelated text
    assert scores[2] > scores[1]
