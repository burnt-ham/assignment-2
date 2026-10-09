"""Quiz UI labels keep the generated concept and target visible and safe."""

from dataclasses import replace

from app import quiz_choice_labels, quiz_coverage_summary, quiz_feedback_html, quiz_question_heading, quiz_ready_header, quiz_result_html
from course_assistant.quiz import Quiz, QuizQuestion
from course_assistant.retrieval import Evidence


def test_quiz_choices_show_scientific_initials_without_added_letters():
    evidence = Evidence("E1", "text", "doc", "species.md", 1, "section", "Species", "Text", "", 1.0)
    options = ("A. fumigatus", "B. subtilis", "C. difficile", "D. radiodurans")
    item = QuizQuestion("Which organism?", options, 0, "Because", evidence, "Text", "Species", "Identify organism")
    assert quiz_choice_labels(item) == options


def test_question_heading_displays_concept_and_target_as_text():
    evidence = Evidence("E1", "text", "doc", "notes.md", 1, "section", "Topic", "Text", "", 1.0)
    item = QuizQuestion(
        question="What <should> be remembered?",
        options=("One", "Two", "Three", "Four"),
        correct_index=0,
        explanation="Because the text says so.",
        evidence=evidence,
        quote="Text",
        concept="<script>alert(1)</script>",
        learning_target="Explain <the> evidence",
    )
    rendered = quiz_question_heading(2, item)
    assert "<strong>2. What &lt;should&gt; be remembered?</strong>" in rendered
    assert "<span>Concept: &lt;script&gt;alert(1)&lt;/script&gt;</span>" in rendered
    assert "<span>Learning target: Explain &lt;the&gt; evidence</span>" in rendered
    assert "<script>" not in rendered


def test_question_heading_renders_markdown_image_syntax_as_plain_html_text():
    evidence = Evidence("E1", "text", "doc", "notes.md", 1, "section", "Topic", "Text", "", 1.0)
    item = QuizQuestion("Name the topic", ("One", "Two", "Three", "Four"), 0, "Because", evidence, "Text", "![track](https://example.com/pixel)", "[click](https://example.com/away)")
    rendered = quiz_question_heading(1, item)
    assert rendered.startswith("<div>")
    assert "<span>Concept: ![track](https://example.com/pixel)</span>" in rendered
    assert "<span>Learning target: [click](https://example.com/away)</span>" in rendered
    assert "<img" not in rendered


def test_coverage_summary_groups_concepts_and_escapes_model_labels():
    evidence = Evidence("E1", "text", "doc", "notes.md", 1, "section", "Topic", "Text", "", 1.0)
    item = QuizQuestion("Question?", ("One", "Two", "Three", "Four"), 0, "Because", evidence, "Text", "RAG", "Identify RAG")
    quiz = Quiz((item, replace(item, concept="rag"), replace(item, concept="<script>")), "", ("notes.md",), "model")
    summary = quiz_coverage_summary(quiz)
    assert "RAG (2)" in summary
    assert "&lt;script&gt; (1)" in summary
    assert "<script>" not in summary


def test_quiz_result_keeps_document_and_concept_markdown_literal():
    evidence = Evidence("E1", "text", "doc", "notes.md", 1, "section", "Topic", "Text", "", 1.0)
    item = QuizQuestion("Question?", ("One", "Two", "Three", "Four"), 0, "Because", evidence, "Text", "![track](https://example.com/pixel)", "Identify RAG")
    quiz = Quiz((item,), "<script>", ("[click](https://example.com/away)",), "model", ("![notice](https://example.com/notice)",))
    rendered = quiz_result_html(quiz)
    assert rendered.startswith("<div>")
    assert "<p>Quiz ready: 1 question from [click](https://example.com/away), topic: &lt;script&gt;</p>" in rendered
    assert "![track](https://example.com/pixel)" in rendered
    assert "![notice](https://example.com/notice)" in rendered
    assert "<script>" not in rendered
    assert "<img" not in rendered


def test_quiz_feedback_keeps_model_markdown_literal_and_html_escaped():
    evidence = Evidence("E1", "text", "doc", "![track](https://example.com/pixel)", 1, "section", "Topic", "Text", "", 1.0)
    item = QuizQuestion("Question?", ("One", "Two", "Three", "Four"), 0, "<script>alert(1)</script> ![track](https://example.com/away)", evidence, "![quote](https://example.com/quote)", "RAG", "Identify RAG")
    rendered = quiz_feedback_html(item, 1)
    assert rendered.startswith("<div>")
    assert "The answer is <strong>One</strong>" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "![track](https://example.com/pixel)" in rendered
    assert "![quote](https://example.com/quote)" in rendered
    assert "<script>" not in rendered
    assert "<img" not in rendered


def test_quiz_ready_header_uses_singular_and_escapes_names():
    evidence = Evidence("E1", "text", "doc", "notes.md", 1, "section", "Topic", "Text", "", 1.0)
    item = QuizQuestion("Question?", ("One", "Two", "Three", "Four"), 0, "Because", evidence, "Text", "RAG", "Identify RAG")
    quiz = Quiz((item,), "<RAG>", ("notes<script>.md",), "model")
    header = quiz_ready_header(quiz)
    assert "1 question from notes&lt;script&gt;.md" in header
    assert "topic: &lt;RAG&gt;" in header
    assert "<script>" not in header
