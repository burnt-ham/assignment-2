"""Unit tests: quiz validation, the fixed answer key, and scoring."""

import dataclasses

import pytest

from course_assistant.quiz import QuizError, QuizModel, grade, offline_questions, validate_questions
from course_assistant.retrieval import Evidence
from conftest import FakeChat

SOURCE = "Chunking divides parsed content into useful passages while preserving source metadata such as page numbers."


def evidence():
    return [Evidence("E1", "text", "d1", "notes.pdf", 2, "page", "Chunking", SOURCE, "", 1.0)]


def question(**changes):
    base = {
        "question": "What does chunking preserve?",
        "options": ["Source metadata", "Model weights", "API keys", "Nothing"],
        "correct_option": "A",
        "explanation": "Chunks keep source metadata.",
        "evidence_id": "E1",
        "quote": "preserving source metadata",
    }
    base.update(changes)
    return base


def test_valid_questions_pass():
    good, problems = validate_questions(QuizModel(questions=[question()]), evidence())
    assert len(good) == 1 and not problems
    assert good[0].correct_letter == "A"


@pytest.mark.parametrize(
    "changes",
    [
        {"options": ["Same", "same", "Other", "Another"]},
        {"correct_option": "E"},
        {"evidence_id": "E7"},
        {"quote": "chunking deletes page numbers"},
    ],
)
def test_bad_questions_are_dropped(changes):
    good, problems = validate_questions(QuizModel(questions=[question(**changes)]), evidence())
    assert good == [] and len(problems) == 1


def test_answer_key_is_fixed_and_scores_match(make_assistant, sample_pdf):
    quote = "retrieves relevant information"  # from page 1, which is E1 when no topic is given
    replies = [{"questions": [
        question(question="What does RAG retrieve?", options=["Relevant information", "Model weights", "Passwords", "Nothing"], quote=quote),
        question(question="What does RAG add to the model context?", options=["Retrieved information", "Random text", "Images only", "Nothing"], correct_option="A", quote=quote),
    ]}]
    assistant = make_assistant(chat=FakeChat(replies))
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    assert len(quiz.questions) == 2
    key, fingerprint = quiz.answer_key, quiz.key_fingerprint
    with pytest.raises(dataclasses.FrozenInstanceError):
        quiz.questions[0].correct_index = 3
    result = grade(quiz, {0: quiz.questions[0].correct_index, 1: (quiz.questions[1].correct_index + 1) % 4})
    assert (result.correct, result.answered, result.total) == (1, 2, len(quiz.questions))
    assert quiz.answer_key == key and quiz.key_fingerprint == fingerprint


def test_unanswered_questions_are_not_scored(make_assistant, sample_pdf):
    assistant = make_assistant()
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    result = grade(quiz, {})
    assert result.correct == 0 and result.answered == 0


def test_offline_questions_are_answerable_from_their_source():
    items = evidence() + [Evidence("E2", "text", "d1", "notes.pdf", 3, "page", "Reranking", "Reranking compares candidate chunks to the original question and prioritizes them.", "", 1.0)]
    questions = offline_questions(items, 2)
    assert questions
    for q in questions:
        answer = q.options[q.correct_index]
        assert answer.lower() in q.evidence.text.lower()
        assert "_____" in q.question and answer.lower() not in q.question.lower()


def test_quiz_needs_a_document(make_assistant):
    with pytest.raises(QuizError, match="Choose at least one document"):
        make_assistant().quiz_maker.make_quiz([], "", 3)


def test_quiz_service_down_uses_offline_questions(make_assistant, sample_pdf):
    assistant = make_assistant(chat=FakeChat(fail=True))
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    assert quiz.source == "offline generator"
    assert any("unavailable" in n for n in quiz.notes)
