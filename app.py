"""Course Assistant: a Gradio app to ask questions about course materials and take practice quizzes.

Run with:  python app.py   then open http://127.0.0.1:7860
"""

from __future__ import annotations

import html
import logging
import os
from pathlib import Path

import gradio as gr

from course_assistant.assistant import CourseAssistant
from course_assistant.config import redact
from course_assistant.ingest import SUPPORTED_DESCRIPTION, SUPPORTED_TYPES, IngestError
from course_assistant.quiz import MAX_QUESTIONS, Quiz, QuizError, QuizQuestion, grade
from course_assistant.services import ServiceError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("course_assistant.app")

CSS = """
.source-gallery img, .quiz-source img { background: #ffffff; border: 1px solid var(--border-color-primary); }
.verified { color: var(--color-accent); font-weight: 600; }
"""


def quiz_choice_labels(item: QuizQuestion) -> tuple[str, ...]:
    """Use each answer verbatim; adding letters could alter scientific names."""
    return item.options


def quiz_question_heading(number: int, item: QuizQuestion) -> str:
    """Display question and study tags as escaped HTML, not Markdown."""
    lines = [f"<strong>{number}. {html.escape(item.question)}</strong>"]
    if item.concept:
        lines.append(f"<span>Concept: {html.escape(item.concept)}</span>")
    if item.learning_target:
        lines.append(f"<span>Learning target: {html.escape(item.learning_target)}</span>")
    return "<div>" + "<br>".join(lines) + "</div>"


def quiz_coverage_summary(quiz: Quiz) -> str:
    """Show which concepts a generated quiz covers, grouping case variants."""
    concepts: dict[str, list] = {}
    for item in quiz.questions:
        label = item.concept.strip() or "Uncategorized"
        key = label.casefold()
        if key not in concepts:
            concepts[key] = [label, 0]
        concepts[key][1] += 1
    return "Concepts covered: " + ", ".join(
        f"{html.escape(label)} ({count})" for label, count in concepts.values()
    )


def quiz_ready_header(quiz: Quiz) -> str:
    count = len(quiz.questions)
    noun = "question" if count == 1 else "questions"
    documents = ", ".join(html.escape(name) for name in quiz.doc_names)
    topic = f", topic: {html.escape(quiz.topic)}" if quiz.topic else ""
    return f"Quiz ready: {count} {noun} from {documents}{topic}"


def quiz_result_html(quiz: Quiz) -> str:
    """Render escaped quiz metadata without interpreting Markdown in model text."""
    notes = "".join(f"<p>ℹ️ {html.escape(note)}</p>" for note in quiz.notes)
    return (
        f"<div><p>{quiz_ready_header(quiz)}</p>"
        f"<p>{quiz_coverage_summary(quiz)}</p>"
        f"<p><small>Written by {html.escape(quiz.source)}</small></p>{notes}</div>"
    )


def quiz_feedback_html(item: QuizQuestion, selected_index: int | None) -> str:
    """Render feedback and citations as escaped HTML rather than Markdown."""
    correct_answer = html.escape(item.options[item.correct_index])
    if selected_index is None:
        verdict = f"The answer is <strong>{correct_answer}</strong>."
    elif selected_index == item.correct_index:
        verdict = "✅ Correct."
    else:
        verdict = f"❌ Not quite. The answer is <strong>{correct_answer}</strong>."
    quote = f"<blockquote>{html.escape(item.quote)}</blockquote>" if item.quote else ""
    return (
        f"<div><p>{verdict} {html.escape(item.explanation)}</p>"
        f"<p><strong>Source:</strong> {html.escape(item.evidence.citation)}</p>{quote}</div>"
    )


def build_app(assistant: CourseAssistant) -> gr.Blocks:
    secrets = assistant.settings.secrets()

    def safe_error(exc: Exception) -> str:
        if isinstance(exc, (IngestError, ServiceError, QuizError)):
            return redact(str(exc), secrets)
        log.error("Unexpected error: %s", redact(repr(exc), secrets))
        return "Something went wrong. Please try again, and report it if it keeps happening."

    # -- shared helpers ------------------------------------------------------

    def doc_choices() -> list[tuple[str, str]]:
        return [(d.name, d.doc_id) for d in assistant.library.list_documents()]

    def doc_table() -> list[list]:
        rows = []
        for d in assistant.library.list_documents():
            rows.append([d.name, d.file_type.upper(), f"{len(d.pages)} {d.page_label}s", len(d.chunks)])
        return rows or [["(no documents yet)", "", "", ""]]

    def refreshed_lists():
        choices = doc_choices()
        return (
            doc_table(),
            gr.update(choices=choices, value=None),
            gr.update(choices=choices),
            gr.update(choices=choices),
        )

    # -- materials -----------------------------------------------------------

    def add_files(files):
        if not files:
            return ("Choose one or more files first.", *refreshed_lists())
        messages = []
        for file in files:
            path = Path(file if isinstance(file, str) else file.name)
            name = path.name
            try:
                result = assistant.library.add_file(path, display_name=name)
                messages.append(("✅ " if result.added else "ℹ️ ") + result.message)
            except Exception as exc:  # one bad file shouldn't stop the others
                messages.append(f"❌ {name}: {safe_error(exc)}")
        return ("\n\n".join(messages), *refreshed_lists())

    def remove_doc(doc_id):
        if not doc_id:
            return ("Choose a document to remove.", *refreshed_lists())
        return (assistant.library.remove(doc_id), *refreshed_lists())

    # -- asking --------------------------------------------------------------

    def ask(question, doc_ids):
        if not question or not question.strip():
            return "Type a question first.", "", [], ""
        try:
            result = assistant.answerer.ask(question, doc_ids or None)
        except Exception as exc:
            return f"❌ {safe_error(exc)}", "", [], ""

        answer_md = html.escape(result.answer).replace("\n", "<br>")
        if result.warnings:
            answer_md += "\n\n" + "\n".join(f"> ⚠️ {html.escape(redact(w, secrets))}" for w in result.warnings)
        answer_md += f"\n\n<sub>Answered in {result.seconds:.1f} s · {html.escape(result.model)}</sub>"

        if result.sources:
            lines = ["### Sources"]
            for source in result.sources:
                mark = "✓ checked" if source.verified else "✗ not confirmed"
                lines.append(f"**{html.escape(source.evidence.citation)}** ({source.evidence.kind}) · <span class='verified'>{mark}</span>")
                if source.quote:
                    lines.append(f"> {html.escape(source.quote)}")
                lines.append("")
            sources_md = "\n".join(lines)
        else:
            sources_md = "_No sources: nothing in the materials supported an answer._" if not result.found else ""

        seen, gallery = set(), []
        for source in result.sources:
            item = source.evidence
            if item.image_path and (item.doc_id, item.page) not in seen:
                seen.add((item.doc_id, item.page))
                gallery.append((item.image_path, item.citation))

        evidence_lines = []
        for item in result.evidence:
            preview = html.escape(item.text.strip().replace("\n", " ")[:220]) or "(image only)"
            evidence_lines.append(f"- **{item.evidence_id}** {html.escape(item.citation)} ({item.kind}, score {item.score:.3f}): {preview}")
        return answer_md, sources_md, gallery, "\n".join(evidence_lines) or "_No evidence found._"

    # -- quizzes -------------------------------------------------------------

    def make_quiz(doc_ids, topic, count):
        try:
            quiz = assistant.quiz_maker.make_quiz(doc_ids or [], topic or "", int(count))
        except Exception as exc:
            return None, {"answers": {}, "revealed": []}, f"<p>❌ {html.escape(safe_error(exc))}</p>"
        return quiz, {"answers": {}, "revealed": []}, quiz_result_html(quiz)

    with gr.Blocks(title="Course Assistant") as app:
        gr.Markdown("# Course Assistant\nAsk questions about your course materials, see the exact slides behind each answer, and take practice quizzes.")

        with gr.Tab("Materials"):
            gr.Markdown(f"**Accepted files:** {SUPPORTED_DESCRIPTION} Uploading the same file twice won't duplicate it.")
            uploader = gr.File(label="Add course files", file_count="multiple", file_types=sorted(SUPPORTED_TYPES))
            add_button = gr.Button("Add to library", variant="primary")
            add_status = gr.Markdown()
            docs = gr.Dataframe(value=doc_table(), headers=["Document", "Type", "Pages", "Text chunks"], interactive=False, label="Library")
            with gr.Row():
                remove_choice = gr.Dropdown(choices=doc_choices(), label="Remove a document", scale=3)
                remove_button = gr.Button("Remove", variant="stop", scale=1)
            with gr.Accordion("AI services in use", open=False):
                gr.Markdown(assistant.status_markdown())

        with gr.Tab("Ask"):
            ask_docs = gr.Dropdown(choices=doc_choices(), multiselect=True, label="Search in (leave empty to search everything)")
            question = gr.Textbox(label="Question", placeholder="e.g. Find the meme about vibe coding on \"Prod\" and explain it", lines=2)
            ask_button = gr.Button("Ask", variant="primary")
            answer = gr.Markdown()
            with gr.Row():
                with gr.Column(scale=1):
                    sources = gr.Markdown()
                with gr.Column(scale=1):
                    gallery = gr.Gallery(label="Source slides and pages", columns=1, height=560, object_fit="contain", elem_classes="source-gallery")
            with gr.Accordion("All evidence the model saw", open=False):
                evidence = gr.Markdown()

        with gr.Tab("Quiz"):
            quiz_docs = gr.Dropdown(choices=doc_choices(), multiselect=True, label="Quiz me on")
            with gr.Row():
                topic = gr.Textbox(label="Topic (optional)", placeholder="e.g. RAG", scale=3)
                count = gr.Slider(1, MAX_QUESTIONS, value=5, step=1, label="Questions", scale=1)
            quiz_button = gr.Button("Make quiz", variant="primary")
            quiz_header = gr.HTML()
            quiz_state = gr.State(None)
            progress = gr.State({"answers": {}, "revealed": []})

            @gr.render(inputs=[quiz_state, progress])
            def render_quiz(quiz, state):
                if quiz is None:
                    return
                answers = {int(k): v for k, v in state.get("answers", {}).items()}
                revealed = set(state.get("revealed", []))
                result = grade(quiz, answers)
                gr.Markdown(f"### Score: {result.correct} of {result.total} correct ({result.answered} answered)")
                for index, item in enumerate(quiz.questions):
                    labels = quiz_choice_labels(item)
                    done = index in answers or index in revealed
                    with gr.Group():
                        gr.HTML(quiz_question_heading(index + 1, item))
                        choice = gr.Radio(
                            choices=labels,
                            value=labels[answers[index]] if index in answers else None,
                            label="Your answer",
                            interactive=not done,
                            key=f"q{index}-{quiz.key_fingerprint}",
                        )
                        if not done:
                            with gr.Row():
                                check = gr.Button("Check answer", size="sm")
                                show = gr.Button("Show answer", size="sm")

                            def on_check(selected, current, i=index, options=labels):
                                current = {"answers": dict(current.get("answers", {})), "revealed": list(current.get("revealed", []))}
                                if selected:
                                    current["answers"][i] = options.index(selected)
                                return current

                            def on_show(current, i=index):
                                current = {"answers": dict(current.get("answers", {})), "revealed": list(current.get("revealed", [])) + [i]}
                                return current

                            check.click(on_check, inputs=[choice, progress], outputs=progress)
                            show.click(on_show, inputs=[progress], outputs=progress)
                        else:
                            gr.HTML(quiz_feedback_html(item, answers.get(index)))
                            source = item.evidence
                            if source.image_path:
                                gr.Image(value=source.image_path, label=source.citation, show_label=True, height=320, elem_classes="quiz-source", interactive=False)

        lists = [docs, remove_choice, ask_docs, quiz_docs]
        add_button.click(add_files, inputs=uploader, outputs=[add_status, *lists])
        remove_button.click(remove_doc, inputs=remove_choice, outputs=[add_status, *lists])
        ask_button.click(ask, inputs=[question, ask_docs], outputs=[answer, sources, gallery, evidence])
        question.submit(ask, inputs=[question, ask_docs], outputs=[answer, sources, gallery, evidence])
        quiz_button.click(make_quiz, inputs=[quiz_docs, topic, count], outputs=[quiz_state, progress, quiz_header])
        app.load(refreshed_lists, outputs=lists)
    return app


def main() -> None:
    assistant = CourseAssistant()
    app = build_app(assistant)
    app.queue().launch(
        server_name=os.getenv("HOST", "127.0.0.1"),
        server_port=int(os.getenv("PORT", "7860")),
        allowed_paths=[str(assistant.settings.data_dir)],
        theme=gr.themes.Soft(),
        css=CSS,
    )


if __name__ == "__main__":
    main()
