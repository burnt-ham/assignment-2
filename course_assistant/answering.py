"""Answer questions from retrieved evidence, with checked citations.

The model must reply with JSON holding two separate fields, `answer` and
`sources`. The reply is validated, and every source is checked:
- it must point at evidence that was actually supplied (no invented citations)
- for text evidence, its quote must really appear in that evidence
If nothing checkable supports the answer, the app says the materials don't
cover the question instead of showing an unsupported answer.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError, field_validator

from .retrieval import Evidence, Retriever
from .services import ChatModel, ServiceError, tokens

NOT_FOUND = "The course materials I have don't cover this, so I can't answer it without guessing."

SYSTEM_PROMPT = """You are a course assistant for an MBA class. Answer the student's question using ONLY the evidence provided (text excerpts and slide or page images).

Rules:
- Use only the evidence. Do not use outside knowledge, and never invent facts, quotes or sources.
- If the evidence does not answer the question, set "found" to false and say briefly that the materials don't cover it.
- When an image is relevant, describe what it actually shows: the people, objects or scene pictured, the structure of a diagram or chart, and what a meme means, not only the words printed on it.
- Cite every piece of evidence you rely on in "sources", using its id (like "E2").
- For text evidence, "quote" must be copied word for word from that evidence (a short phrase or sentence).
- For image evidence, "quote" may be a short description of what the image shows.

Reply with JSON only, in exactly this shape:
{"found": true, "answer": "your answer", "sources": [{"evidence_id": "E1", "quote": "exact words from E1"}]}"""


class SourceModel(BaseModel):
    evidence_id: str
    quote: str = ""


class AnswerModel(BaseModel):
    found: bool = True
    answer: str = Field(min_length=1)
    sources: list[SourceModel] = Field(default_factory=list)

    @field_validator("sources", mode="before")
    @classmethod
    def _bare_ids(cls, value):
        """Accept sources given as bare ids, e.g. ["E1"], so one missing quote doesn't
        discard the whole reply. A bare id has no quote, so it counts as support only
        for image evidence; a text source without a quote can't be checked and fails."""
        if isinstance(value, list):
            return [{"evidence_id": s} if isinstance(s, str) else s for s in value]
        return value


@dataclass
class CheckedSource:
    evidence: Evidence
    quote: str
    verified: bool
    note: str


@dataclass
class AnswerResult:
    question: str
    answer: str
    found: bool
    sources: list[CheckedSource]
    evidence: list[Evidence]
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    model: str = ""

    def to_dict(self) -> dict:
        """The structured result: separate `answer` and `sources` fields."""
        return {
            "answer": self.answer,
            "found": self.found,
            "sources": [
                {
                    "document": s.evidence.doc_name,
                    "location": s.evidence.location,
                    "kind": s.evidence.kind,
                    "quote": s.quote,
                    "verified": s.verified,
                    "image": s.evidence.image_path,
                }
                for s in self.sources
            ],
        }


_LEADING_REASONING = re.compile(r"\s*<think>.*?</think>", re.S)


def _top_level_objects(text: str) -> list[dict]:
    """Every JSON object in the text that isn't nested inside another one, in order."""
    decoder = json.JSONDecoder()
    found, pos = [], 0
    while (start := text.find("{", pos)) != -1:
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            pos = start + 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        pos = end
    return found


def extract_json(text: str) -> dict:
    """Pull the model's JSON object out of its reply.

    Tolerates code fences, extra words and a reasoning model's <think>...</think>
    block. Only a reasoning block at the start of the reply is removed, so text
    inside the answer is never rewritten. If the reply holds several objects
    (for example an example followed by the real answer), the last one wins.
    """
    leading = _LEADING_REASONING.match(text)
    if leading:
        # Fall back to the whole reply if nothing usable follows the reasoning block,
        # e.g. when the model put its JSON inside the block.
        candidates = [text[leading.end():], text]
    elif "</think>" in text and "<think>" not in text:
        # Some chat templates add the opening tag themselves, so the reply starts with
        # reasoning and only "</think>" appears. A "</think>" inside an answer string
        # is also possible, so fall back to the whole reply if nothing follows the tag.
        candidates = [text.split("</think>", 1)[1], text]
    else:
        candidates = [text]
    for candidate in candidates:
        candidate = candidate.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.S)
        if fenced:
            candidate = fenced.group(1).strip()
        objects = _top_level_objects(candidate)
        if objects:
            return objects[-1]
    raise ValueError("no JSON object in reply")


def _normalize(text: str) -> str:
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip(" .\"'")


def quote_supported(quote: str, evidence_text: str) -> bool:
    """True if the quote appears in the evidence (exactly, or with nearly all its words)."""
    quote_n, evidence_n = _normalize(quote), _normalize(evidence_text)
    if not quote_n:
        return False
    if quote_n in evidence_n:
        return True
    quote_words = tokens(quote_n)
    if len(quote_words) < 3:
        return False
    evidence_words = set(tokens(evidence_n))
    return sum(w in evidence_words for w in quote_words) / len(quote_words) >= 0.85


def check_sources(parsed: AnswerModel, evidence: list[Evidence]) -> tuple[list[CheckedSource], list[str]]:
    by_id = {e.evidence_id: e for e in evidence}
    checked: list[CheckedSource] = []
    problems: list[str] = []
    seen = set()
    for source in parsed.sources:
        item = by_id.get(source.evidence_id.strip().upper())
        if item is None:
            problems.append(f"The model cited {source.evidence_id!r}, which wasn't in the evidence, so it was dropped.")
            continue
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        if item.kind == "image":
            ok = True
            note = "Image evidence: compare the slide image with the answer."
            if source.quote and quote_supported(source.quote, item.text):
                note = "Quote found in the slide's text."
        else:
            ok = quote_supported(source.quote, item.text)
            if ok:
                note = "Quote found in the source."
            elif not source.quote.strip():
                note = "No quote was given, so this text source couldn't be checked."
            else:
                note = "Quote not found in the source text."
        checked.append(CheckedSource(item, source.quote, ok, note))
    return checked, problems


def question_reminder(question: str) -> str:
    """Repeated after the evidence and images. With the question only at the top of a long
    message, the class chat model sometimes answered a different question found in the evidence."""
    return f"Question (repeated): {question}\nAnswer this question using only the evidence above. Reply with the JSON object only."


def build_user_message(question: str, evidence: list[Evidence]) -> tuple[str, list[str]]:
    lines = [f"Question: {question}", "", "Evidence:"]
    images: list[str] = []
    for item in evidence:
        header = f"[{item.evidence_id}] {item.citation}"
        if item.kind == "image":
            images.append(item.image_path)
            lines.append(f"{header} (image #{len(images)} attached below)")
            if item.text.strip():
                lines.append(f"Text on this {item.label}: {item.text.strip()[:1500]}")
            else:
                lines.append(f"This {item.label} has no text; use the image.")
        else:
            lines.append(f"{header} (text excerpt)")
            lines.append(item.text.strip())
        lines.append("")
    if not evidence:
        lines.append("(no evidence found)")
    return "\n".join(lines), images


class Answerer:
    def __init__(self, retriever: Retriever, chat_model: ChatModel | None):
        self.retriever = retriever
        self.chat_model = chat_model

    def ask(self, question: str, doc_ids: list[str] | None = None) -> AnswerResult:
        started = time.perf_counter()
        question = question.strip()
        retrieval = self.retriever.search(question, doc_ids)
        evidence = retrieval.evidence
        warnings = list(retrieval.warnings)
        if not evidence:
            result = AnswerResult(question, NOT_FOUND, False, [], [], warnings)
        elif self.chat_model is None:
            result = self._offline_answer(question, evidence, warnings)
        else:
            result = self._model_answer(question, evidence, warnings)
        result.seconds = time.perf_counter() - started
        return result

    def _model_answer(self, question: str, evidence: list[Evidence], warnings: list[str]) -> AnswerResult:
        user, images = build_user_message(question, evidence)
        parsed = None
        empty_replies = 0
        for attempt in range(2):
            prompt = user if attempt == 0 else user + "\n\nYour last reply was empty or was not valid JSON in the required shape. Reply with the JSON object only."
            try:
                reply = self.chat_model.complete(SYSTEM_PROMPT, prompt, images, after_images=question_reminder(question))
            except ServiceError as exc:
                warnings.append(f"The answer service is unavailable: {exc}")
                result = self._offline_answer(question, evidence, warnings)
                result.answer = "The AI answer service is unavailable right now. These are the most relevant passages I found:\n\n" + result.answer
                return result
            if not reply.strip():
                empty_replies += 1
                continue
            try:
                parsed = AnswerModel.model_validate(extract_json(reply))
                break
            except (ValueError, ValidationError):
                continue
        if parsed is None:
            if empty_replies:
                warnings.append("The model returned an empty reply. It may have used its whole token budget before writing an answer.")
            warnings.append("The model's reply couldn't be read as a valid answer, even after a retry.")
            return AnswerResult(question, "Sorry, I couldn't produce a valid answer. Please try again.", False, [], evidence, warnings, model=self.chat_model.name)

        sources, problems = check_sources(parsed, evidence)
        warnings.extend(problems)
        if not parsed.found:
            return AnswerResult(question, parsed.answer or NOT_FOUND, False, [], evidence, warnings, model=self.chat_model.name)
        if not any(s.verified for s in sources):
            warnings.append("The answer wasn't backed by any checkable source, so it was withheld.")
            return AnswerResult(question, NOT_FOUND, False, sources, evidence, warnings, model=self.chat_model.name)
        return AnswerResult(question, parsed.answer, True, sources, evidence, warnings, model=self.chat_model.name)

    def _offline_answer(self, question: str, evidence: list[Evidence], warnings: list[str]) -> AnswerResult:
        """No chat model: return the best matching passages, quoted, instead of a written answer."""
        query_words = set(tokens(question))
        relevant = []
        for item in evidence:
            overlap = len(query_words & set(tokens(item.text))) / (len(query_words) or 1)
            if overlap >= 0.34 and item.text.strip():
                relevant.append(item)
        if not relevant:
            return AnswerResult(question, NOT_FOUND, False, [], evidence, warnings, model="offline (no chat model)")
        chosen, seen_pages = [], set()
        for item in relevant:
            if (item.doc_id, item.page) not in seen_pages:
                seen_pages.add((item.doc_id, item.page))
                chosen.append(item)
        chosen = chosen[:2]
        parts = []
        sources = []
        for item in chosen:
            excerpt = item.text.strip().replace("\n", " ")[:400]
            parts.append(f"From {item.citation}: “{excerpt}”")
            sources.append(CheckedSource(item, excerpt, True, "Quoted directly from the source."))
        answer = "Offline mode (no AI model connected): here are the most relevant passages.\n\n" + "\n\n".join(parts)
        return AnswerResult(question, answer, True, sources, evidence, warnings, model="offline (no chat model)")
