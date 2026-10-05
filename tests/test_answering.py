"""Unit tests: structured answers, citation checks, and missing information."""

import pytest

from course_assistant.answering import NOT_FOUND, AnswerModel, check_sources, extract_json, quote_supported
from course_assistant.retrieval import Evidence
from conftest import FakeChat


def evidence(eid="E1", kind="text", text="Reranking compares candidate chunks to the original question."):
    return Evidence(eid, kind, "d1", "Week 5.pptx", 18, "slide", "Hybrid RAG", text, "/tmp/x.png", 1.0)


def test_extract_json_handles_code_fences_and_extra_words():
    assert extract_json('Sure!\n```json\n{"answer": "x", "sources": []}\n```') == {"answer": "x", "sources": []}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_quote_check():
    source = "Reranking compares candidate chunks to the original question and prioritizes them."
    assert quote_supported("compares candidate chunks to the original question", source)
    assert quote_supported("Reranking compares candidate  chunks", source)  # spacing differences are fine
    assert not quote_supported("Reranking is always slower than keyword search", source)
    assert not quote_supported("", source)


def test_invented_citations_are_dropped():
    parsed = AnswerModel(answer="x", sources=[{"evidence_id": "E1", "quote": "compares candidate chunks"}, {"evidence_id": "E9", "quote": "made up"}])
    checked, problems = check_sources(parsed, [evidence()])
    assert [c.evidence.evidence_id for c in checked] == ["E1"] and checked[0].verified
    assert any("E9" in p for p in problems)


def test_image_evidence_counts_as_visual_support():
    parsed = AnswerModel(answer="x", sources=[{"evidence_id": "E1", "quote": "a meme of a burning server room"}])
    checked, _ = check_sources(parsed, [evidence(kind="image", text="")])
    assert checked[0].verified


def test_answer_has_separate_answer_and_sources_fields(make_assistant, sample_pdf):
    chat = FakeChat([{"found": True, "answer": "It re-orders candidates by relevance.", "sources": [{"evidence_id": "E1", "quote": "Reranking compares candidate chunks"}]}])
    assistant = make_assistant(chat=chat)
    assistant.library.add_file(sample_pdf)
    result = assistant.answerer.ask("What does reranking do?")
    data = result.to_dict()
    assert data["answer"] == "It re-orders candidates by relevance."
    assert data["sources"][0]["document"] == "rag-notes.pdf" and data["sources"][0]["verified"]
    assert chat.calls[0]["images"], "relevant page images should be sent to the model"


def test_unsupported_answer_is_withheld(make_assistant, sample_pdf):
    chat = FakeChat([{"found": True, "answer": "Reranking was invented in 1802.", "sources": [{"evidence_id": "E1", "quote": "invented in 1802"}]}])
    assistant = make_assistant(chat=chat)
    assistant.library.add_file(sample_pdf)
    result = assistant.answerer.ask("What does reranking do?")
    assert result.answer == NOT_FOUND and not result.found


def test_model_saying_not_found_is_respected(make_assistant, sample_pdf):
    chat = FakeChat([{"found": False, "answer": "The materials don't cover the capital of France.", "sources": []}])
    assistant = make_assistant(chat=chat)
    assistant.library.add_file(sample_pdf)
    result = assistant.answerer.ask("What is the capital of France?")
    assert not result.found and result.sources == []


def test_invalid_reply_is_retried_once(make_assistant, sample_pdf):
    good = {"found": True, "answer": "Passages that keep source metadata.", "sources": [{"evidence_id": "E1", "quote": "preserving source metadata"}]}
    chat = FakeChat(["not json at all", good])
    assistant = make_assistant(chat=chat)
    assistant.library.add_file(sample_pdf)
    result = assistant.answerer.ask("What does chunking preserve?")
    assert len(chat.calls) == 2
    assert result.found or "valid" in result.answer


def test_unavailable_chat_service_falls_back_to_passages(make_assistant, sample_pdf):
    assistant = make_assistant(chat=FakeChat(fail=True))
    assistant.library.add_file(sample_pdf)
    result = assistant.answerer.ask("What does chunking preserve?")
    assert "unavailable" in result.answer
    assert any("unavailable" in w for w in result.warnings)


def test_offline_mode_admits_missing_information(make_assistant, sample_pdf):
    assistant = make_assistant()
    assistant.library.add_file(sample_pdf)
    assert assistant.answerer.ask("Who won the 1998 World Cup?").answer == NOT_FOUND


def test_empty_library_admits_missing_information(make_assistant):
    assert make_assistant().answerer.ask("What is RAG?").answer == NOT_FOUND
