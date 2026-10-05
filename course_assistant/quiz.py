"""Practice quizzes: multiple-choice questions from chosen materials, with a fixed answer key.

The answer key is set when the quiz is created and cannot change afterwards
(questions are frozen). Every question keeps the evidence it came from, so
feedback can show the source excerpt or slide image.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from .answering import extract_json, quote_supported
from .library import Library
from .retrieval import Evidence, Retriever
from .services import ChatModel, ServiceError, tokens

LETTERS = "ABCD"
MAX_QUESTIONS = 10

QUIZ_SYSTEM_PROMPT = """You write practice quizzes for an MBA course. Write multiple-choice questions using ONLY the evidence provided.

Rules:
- Every question must be answerable from one piece of evidence. Do not use outside knowledge.
- Each question has exactly 4 options, with exactly one correct option. Wrong options must be plausible but clearly wrong according to the evidence.
- "correct_option" is the letter of the correct option: "A", "B", "C" or "D". Vary which letter is correct.
- "explanation" says why the correct option is right, in one or two sentences.
- "evidence_id" names the evidence the question comes from (like "E3").
- "quote" is a short phrase copied word for word from that evidence that supports the correct answer. For image evidence, describe what the image shows instead.

Reply with JSON only, in exactly this shape:
{"questions": [{"question": "...", "options": ["...", "...", "...", "..."], "correct_option": "B", "explanation": "...", "evidence_id": "E1", "quote": "..."}]}"""


class QuestionModel(BaseModel):
    question: str = Field(min_length=5)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option: str
    explanation: str = Field(min_length=1)
    evidence_id: str
    quote: str = ""


class QuizModel(BaseModel):
    questions: list[QuestionModel]


class QuizError(Exception):
    """A quiz couldn't be made. The message is safe to show."""


@dataclass(frozen=True, eq=False)
class QuizQuestion:
    question: str
    options: tuple[str, ...]
    correct_index: int
    explanation: str
    evidence: Evidence
    quote: str

    @property
    def correct_letter(self) -> str:
        return LETTERS[self.correct_index]


@dataclass(frozen=True, eq=False)
class Quiz:
    questions: tuple[QuizQuestion, ...]
    topic: str
    doc_names: tuple[str, ...]
    source: str  # which model wrote it
    notes: tuple[str, ...] = ()

    @property
    def answer_key(self) -> tuple[int, ...]:
        return tuple(q.correct_index for q in self.questions)

    @property
    def key_fingerprint(self) -> str:
        """A short fingerprint of the answer key, to show it never changes."""
        raw = "|".join(f"{q.question}={q.correct_index}" for q in self.questions)
        return hashlib.sha256(raw.encode()).hexdigest()[:10]


@dataclass
class Grade:
    correct: int
    answered: int
    total: int
    per_question: dict[int, bool]

    @property
    def summary(self) -> str:
        return f"{self.correct} of {self.total} correct" + (f" ({self.answered} answered)" if self.answered < self.total else "")


def grade(quiz: Quiz, responses: dict[int, int | None]) -> Grade:
    """Score answers against the fixed key. `responses` maps question index to chosen option index."""
    per_question = {}
    for index, question in enumerate(quiz.questions):
        choice = responses.get(index)
        if choice is not None:
            per_question[index] = choice == question.correct_index
    return Grade(
        correct=sum(per_question.values()),
        answered=len(per_question),
        total=len(quiz.questions),
        per_question=per_question,
    )


def _letter_to_index(value: str) -> int | None:
    value = value.strip().upper().rstrip(").:")
    if value in LETTERS:
        return LETTERS.index(value)
    if value.isdigit() and 0 <= int(value) - 1 < 4:
        return int(value) - 1
    return None


def validate_questions(parsed: QuizModel, evidence: list[Evidence]) -> tuple[list[QuizQuestion], list[str]]:
    by_id = {e.evidence_id: e for e in evidence}
    good: list[QuizQuestion] = []
    problems: list[str] = []
    seen = set()
    for number, item in enumerate(parsed.questions, start=1):
        options = tuple(o.strip() for o in item.options)
        correct = _letter_to_index(item.correct_option)
        source = by_id.get(item.evidence_id.strip().upper())
        reason = None
        if any(not o for o in options) or len({o.lower() for o in options}) < 4:
            reason = "its options were blank or repeated"
        elif correct is None:
            reason = "it had no valid correct option"
        elif source is None:
            reason = "it cited evidence that wasn't supplied"
        elif source.kind == "text" and not quote_supported(item.quote, source.text):
            reason = "its supporting quote wasn't found in the source"
        elif item.question.strip().lower() in seen:
            reason = "it repeated another question"
        if reason:
            problems.append(f"Question {number} was dropped because {reason}.")
            continue
        seen.add(item.question.strip().lower())
        good.append(QuizQuestion(item.question.strip(), options, correct, item.explanation.strip(), source, item.quote.strip()))
    return good, problems


class QuizMaker:
    def __init__(self, library: Library, retriever: Retriever, chat_model: ChatModel | None):
        self.library = library
        self.retriever = retriever
        self.chat_model = chat_model

    def gather_evidence(self, doc_ids: list[str], topic: str, limit: int = 12) -> list[Evidence]:
        if topic.strip():
            saved = (self.retriever.max_text, self.retriever.max_images)
            self.retriever.max_text, self.retriever.max_images = limit, 2
            try:
                evidence = self.retriever.search(topic, doc_ids).evidence
            finally:
                self.retriever.max_text, self.retriever.max_images = saved
            return evidence
        # No topic: spread chunks evenly across the chosen documents.
        chunks = self.library.all_chunks(doc_ids)
        chunks = [c for c in chunks if len(c.text) > 80] or chunks
        if not chunks:
            return []
        step = max(1, len(chunks) // limit)
        picked = chunks[::step][:limit]
        return [
            Evidence(f"E{i}", "text", c.doc_id, c.doc_name, c.page, c.label, c.title, c.text, c.image_path, 0.0)
            for i, c in enumerate(picked, start=1)
        ]

    def make_quiz(self, doc_ids: list[str], topic: str = "", count: int = 5) -> Quiz:
        if not doc_ids:
            raise QuizError("Choose at least one document for the quiz.")
        count = max(1, min(MAX_QUESTIONS, int(count)))
        evidence = self.gather_evidence(doc_ids, topic, limit=max(count + 4, 8))
        if not evidence:
            message = "No material matched that topic in the chosen documents." if topic.strip() else "The chosen documents have no text to quiz on."
            raise QuizError(message)
        names = tuple(sorted({e.doc_name for e in evidence}))
        notes: list[str] = []
        if self.chat_model is not None:
            try:
                questions, problems = self._model_questions(evidence, count, topic)
                notes.extend(problems)
                source = self.chat_model.name
            except ServiceError as exc:
                notes.append(f"The quiz service is unavailable ({exc}), so simpler offline questions were made instead.")
                questions, source = offline_questions(evidence, count), "offline generator"
        else:
            questions, source = offline_questions(evidence, count), "offline generator"
        if not questions:
            raise QuizError("I couldn't write valid questions from that material. Try other documents or a broader topic.")
        if len(questions) < count:
            notes.append(f"Only {len(questions)} of the {count} requested questions passed the checks.")
        return Quiz(tuple(questions[:count]), topic.strip(), names, source, tuple(notes))

    def _model_questions(self, evidence: list[Evidence], count: int, topic: str) -> tuple[list[QuizQuestion], list[str]]:
        from .answering import build_user_message

        request = f"Write {count} multiple-choice questions" + (f" about: {topic}" if topic.strip() else " covering the evidence")
        user, images = build_user_message(request, evidence)
        last_problems: list[str] = []
        for attempt in range(2):
            prompt = user if attempt == 0 else user + "\n\nYour last reply was not valid JSON in the required shape. Reply with the JSON object only."
            reply = self.chat_model.complete(QUIZ_SYSTEM_PROMPT, prompt, images, max_tokens=4000)
            try:
                parsed = QuizModel.model_validate(extract_json(reply))
            except (ValueError, ValidationError):
                last_problems = ["The model's quiz reply couldn't be read, even after a retry."]
                continue
            return validate_questions(parsed, evidence)
        return [], last_problems


# ---------------------------------------------------------------------------
# Offline questions (no chat model): fill-in-the-blank from the source text
# ---------------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_WRAPPED_LINE = re.compile(r"\n(?!\s*[•\-\u2022\u25aa])(?=\s*[a-z(])")


def _sentences(text: str) -> list[str]:
    """Split slide text into sentences, re-joining lines that were wrapped mid-sentence."""
    text = _WRAPPED_LINE.sub(" ", text)
    return [s.strip(" •\t") for s in _SENTENCE.split(text) if len(s.split()) >= 6]


def _key_terms(text: str) -> list[str]:
    return [w for w in tokens(text) if len(w) >= 6 and not w.isdigit()]


def offline_questions(evidence: list[Evidence], count: int, seed: int = 7) -> list[QuizQuestion]:
    rng = random.Random(seed)
    vocabulary = sorted({w for e in evidence for w in _key_terms(e.text)})
    questions: list[QuizQuestion] = []
    used_terms = set()
    for item in evidence:
        if len(questions) >= count:
            break
        sentences = _sentences(item.text)
        for sentence in sentences:
            terms = [t for t in _key_terms(sentence) if t not in used_terms]
            distractors = [w for w in vocabulary if w not in set(tokens(sentence))]
            if not terms or len(distractors) < 3:
                continue
            answer = max(terms, key=len)
            pattern = re.compile(re.escape(answer), re.IGNORECASE)
            if not pattern.search(sentence):
                continue
            blanked = pattern.sub("_____", sentence, count=1)
            options = [answer] + rng.sample(distractors, 3)
            rng.shuffle(options)
            used_terms.add(answer)
            questions.append(
                QuizQuestion(
                    question=f"Fill in the blank: “{blanked}”",
                    options=tuple(options),
                    correct_index=options.index(answer),
                    explanation=f"The missing word is “{answer}”, as the source below shows.",
                    evidence=item,
                    quote=sentence,
                )
            )
            break
    return questions
