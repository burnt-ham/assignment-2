"""End-to-end tests: complete workflows through the assistant, with fake AI services."""

import shutil

import gradio as gr

from app import build_app
from course_assistant.answering import NOT_FOUND
from course_assistant.quiz import grade
from conftest import FakeChat, FailingReranker, make_pdf


def answer_reply(quote):
    return {"found": True, "answer": "Answer based on the notes.", "sources": [{"evidence_id": "E1", "quote": quote}]}


def test_full_workflow_add_ask_quiz_remove(make_assistant, sample_pdf, sample_md, tmp_path):
    chat = FakeChat()
    assistant = make_assistant(chat=chat)

    # Add two documents, then upload one again under another name.
    pdf = assistant.library.add_file(sample_pdf).document
    md = assistant.library.add_file(sample_md).document
    copy = tmp_path / "copy.md"
    shutil.copyfile(sample_md, copy)
    assert not assistant.library.add_file(copy).added
    assert len(assistant.library.list_documents()) == 2

    # Ask a text question: the answer cites the syllabus section.
    chat.replies = [answer_reply("Tuesdays and Fridays from 11am to 12pm")]
    result = assistant.answerer.ask("When are office hours?")
    assert result.found and result.sources[0].evidence.doc_name == "syllabus.md"

    # Make a quiz from the PDF and score it.
    chat.replies = [{"questions": [{
        "question": "What does reranking prioritize?",
        "options": ["Which chunks are supplied", "Font sizes", "File names", "Upload order"],
        "correct_option": "A",
        "explanation": "Reranking prioritizes which chunks are supplied.",
        "evidence_id": "E1",
        "quote": "prioritizes which chunks are supplied",
    }]}]
    quiz = assistant.quiz_maker.make_quiz([pdf.doc_id], topic="reranking", count=1)
    assert quiz.questions[0].evidence.doc_id == pdf.doc_id
    assert grade(quiz, {0: 0}).correct == 1

    # Remove the syllabus: later answers can't use it.
    assistant.library.remove(md.doc_id)
    chat.replies = [answer_reply("Tuesdays and Fridays from 11am to 12pm")]
    after = assistant.answerer.ask("When are office hours?")
    assert all(s.evidence.doc_id != md.doc_id for s in after.sources)
    assert all(e.doc_id != md.doc_id for e in after.evidence)


def test_visual_question_sends_slide_images_and_returns_them(make_assistant, tmp_path):
    deck = make_pdf(tmp_path / "week2.pdf", ["Vibe coding overview", "Vibe Coding on Prod\nA meme about shipping straight to production", "Security"])
    chat = FakeChat([{"found": True, "answer": "The slide shows a meme about deploying to production.", "sources": [{"evidence_id": "E1", "quote": "meme about shipping"}]}])
    assistant = make_assistant(chat=chat)
    assistant.library.add_file(deck)
    result = assistant.answerer.ask("Find the meme about vibe coding on prod")
    assert result.found
    assert any(e.kind == "image" and e.page == 2 for e in result.evidence)
    assert chat.calls[0]["images"]
    assert result.sources[0].evidence.image_path


def test_everything_still_works_when_services_are_down(make_assistant, sample_pdf):
    assistant = make_assistant(chat=FakeChat(fail=True), reranker=FailingReranker(), use_rerank=True)
    doc = assistant.library.add_file(sample_pdf).document
    result = assistant.answerer.ask("What does chunking preserve?")
    assert "unavailable" in result.answer
    assert any("Reranking was skipped" in w for w in result.warnings)
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=1)
    assert quiz.questions


def test_missing_information_is_acknowledged(make_assistant, sample_pdf):
    chat = FakeChat([{"found": False, "answer": "The materials don't say.", "sources": []}])
    assistant = make_assistant(chat=chat)
    assistant.library.add_file(sample_pdf)
    assert not assistant.answerer.ask("What is the parking policy on campus?").found
    assert make_assistant().answerer.ask("anything at all") .answer == NOT_FOUND


def test_interface_builds(make_assistant):
    assert isinstance(build_app(make_assistant()), gr.Blocks)
