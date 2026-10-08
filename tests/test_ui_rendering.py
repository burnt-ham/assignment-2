"""Untrusted course and model text must remain literal in the UI."""
from pathlib import Path
from types import SimpleNamespace

import app as ui
from app import build_app
from conftest import FakeChat
from course_assistant.quiz import QuizQuestion
from course_assistant.retrieval import Evidence


def unsafe_question():
    evidence = Evidence("E1", "text", "d1", "![source](https://example.com/source)", 2, "page", "RAG", "Evidence text", "", 1.0)
    return QuizQuestion("![prompt](https://example.com/prompt) <script>alert(1)</script>",
                        ("A", "B", "C", "D"), 0,
                        "![feedback](https://example.com/feedback) <script>alert(1)</script>",
                        evidence, "![quote](https://example.com/quote)")


def test_quiz_question_heading_is_literal_html():
    heading = ui.quiz_question_heading(1, unsafe_question())
    assert "![prompt](https://example.com/prompt)" in heading
    assert "&lt;script&gt;" in heading and "<script>" not in heading


def test_quiz_feedback_and_citation_are_literal_html():
    feedback = ui.quiz_feedback_html(unsafe_question(), 1)
    assert "![feedback](https://example.com/feedback)" in feedback
    assert "![quote](https://example.com/quote)" in feedback
    assert "![source](https://example.com/source)" in feedback
    assert "&lt;script&gt;" in feedback and "<script>" not in feedback


def test_service_status_uses_literal_text_for_model_names(make_assistant):
    assistant = make_assistant()
    assistant.services.status = [("![role](https://example.com/role)",
                                  "![model](https://example.com/model) <script>alert(1)</script>", True)]
    app = build_app(assistant)
    matching = [component for component in app.config["components"]
                if "![model](https://example.com/model)" in str(component.get("props", {}).get("value", ""))]
    assert len(matching) == 1
    assert matching[0]["type"] == "html"
    assert "&lt;script&gt;" in matching[0]["props"]["value"]
    assert "<script>" not in matching[0]["props"]["value"]


def test_material_status_displays_upload_message_as_literal_text(make_assistant, sample_md, monkeypatch):
    assistant = make_assistant()
    monkeypatch.setattr(assistant.library, "add_file", lambda *args, **kwargs: SimpleNamespace(
        added=True, message="![probe](https://example.com/pixel) <script>alert(1)</script>"))
    app = build_app(assistant)
    components = {c["id"]: c for c in app.config["components"]}
    callback = next(d for d in app.config["dependencies"]
                    if any(components[t[0]]["props"].get("value") == "Add to library"
                           for t in d["targets"] if t[0] in components))
    assert components[callback["outputs"][0]]["type"] == "html"
    status = app.fns[callback["id"]].fn([str(sample_md)], "default")[0]
    assert "![probe](https://example.com/pixel)" in status
    assert "&lt;script&gt;" in status and "<script>" not in status


def test_quiz_header_keeps_untrusted_document_name_literal(make_assistant, sample_pdf):
    chat = FakeChat([{"questions": [{
        "question": "What does RAG retrieve? ![prompt](https://example.com/prompt)",
        "options": ["Relevant information", "Model weights", "Passwords", "Nothing"],
        "correct_option": "A",
        "explanation": "![feedback](https://example.com/feedback) <script>alert(1)</script>",
        "evidence_id": "E1", "quote": "retrieves relevant information",
        "concept": "Retrieval-augmented generation",
        "learning_target": "Explain what RAG retrieves before answering a question",
    }]}])
    assistant = make_assistant(chat=chat)
    document = assistant.library.add_file(sample_pdf, display_name="![source](https://example.com/source)").document
    app = build_app(assistant)
    components = {c["id"]: c for c in app.config["components"]}
    callback = next(d for d in app.config["dependencies"]
                    if any(components[t[0]]["props"].get("value") == "Make quiz"
                           for t in d["targets"] if t[0] in components))
    assert components[callback["outputs"][2]]["type"] == "html"
    quiz, _, header = app.fns[callback["id"]].fn([document.doc_id], "", 1, "default")
    assert "![source](https://example.com/source)" in header
    assert quiz.questions
    assert "![prompt](https://example.com/prompt)" in quiz.questions[0].question


def test_ask_answer_sources_and_evidence_do_not_parse_untrusted_markdown(make_assistant, tmp_path: Path):
    note = tmp_path / "source.md"
    note.write_text("# RAG\nRAG retrieves relevant information from documents. ![evidence](https://example.com/evidence)\n", encoding="utf-8")
    chat = FakeChat([{"found": True,
                      "answer": "![model](https://example.com/model) <script>alert(1)</script>",
                      "sources": [{"evidence_id": "E1", "quote": "RAG retrieves relevant information"}]}])
    assistant = make_assistant(chat=chat)
    document = assistant.library.add_file(note, display_name="![source](https://example.com/source)").document
    app = build_app(assistant)
    components = {c["id"]: c for c in app.config["components"]}
    callback = next(d for d in app.config["dependencies"]
                    if any(components[t[0]]["props"].get("value") == "Ask"
                           for t in d["targets"] if t[0] in components))
    assert all(components[i]["type"] == "html" for i in (callback["outputs"][0], callback["outputs"][1], callback["outputs"][3]))
    answer, sources, _, evidence = app.fns[callback["id"]].fn("What does RAG retrieve?", [document.doc_id], "default")
    assert "![model](https://example.com/model)" in answer
    assert "&lt;script&gt;" in answer and "<script>" not in answer
    assert "![source](https://example.com/source)" in sources
    assert "![evidence](https://example.com/evidence)" in evidence
