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
from course_assistant.quiz import LETTERS, MAX_QUESTIONS, QuizError, QuizQuestion, grade
from course_assistant.services import ServiceError
from course_assistant.study_spaces import StudySpaceError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("course_assistant.app")

CSS = """
.source-gallery img, .quiz-source img { background: #ffffff; border: 1px solid var(--border-color-primary); }
.verified { color: var(--color-accent); font-weight: 600; }
"""


def quiz_question_heading(number: int, item: QuizQuestion) -> str:
    return f"<strong>{number}. {html.escape(item.question)}</strong>"


def quiz_feedback_html(item: QuizQuestion, selected_index: int | None) -> str:
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
        if isinstance(exc, (IngestError, ServiceError, QuizError, StudySpaceError)):
            return redact(str(exc), secrets)
        log.error("Unexpected error: %s", redact(repr(exc), secrets))
        return "Something went wrong. Please try again, and report it if it keeps happening."

    # -- shared helpers ------------------------------------------------------

    def service_status_html() -> str:
        rows = [f"<tr><td>{html.escape(role)}</td><td>{html.escape(description)}</td>"
                f"<td>{'class service' if real else 'offline stand-in'}</td></tr>"
                for role, description, real in assistant.services.status]
        mode = ("keyword + text + image search" if assistant.settings.retrieval_mode != "embeddings_only"
                else "text + image search (no keyword search)")
        rows.append(f"<tr><td>Search mode</td><td>{mode}</td><td></td></tr>")
        return "<table><thead><tr><th>Part</th><th>Using</th><th></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"

    def doc_choices() -> list[tuple[str, str]]:
        return [(d.name, d.doc_id) for d in assistant.library.list_documents()]

    def doc_table() -> list[list]:
        rows = []
        for d in assistant.library.list_documents():
            rows.append([d.name, d.file_type.upper(), f"{len(d.pages)} {d.page_label}s", len(d.chunks)])
        return rows or [["(no documents yet)", "", "", ""]]

    def refreshed_lists(clear_selections=False):
        choices = doc_choices()
        selected = {"value": None} if clear_selections else {}
        return (
            doc_table(),
            gr.update(choices=choices, value=None),
            gr.update(choices=choices, **selected),
            gr.update(choices=choices, **selected),
        )

    def space_choices():
        return [(space.name, space.space_id) for space in assistant.study_spaces.list()]

    def deletable_choices():
        return [(name, space_id) for name, space_id in space_choices()
                if space_id != assistant.study_spaces.active_id]

    def refresh_space(heading=""):
        """Discard all results and selections from the previously active space."""
        return (
            gr.update(choices=space_choices(), value=assistant.study_spaces.active_id),
            f"<span>{html.escape(heading)}</span>" if heading else f"Active: <strong>{html.escape(assistant.study_spaces.active.name)}</strong>",
            gr.update(choices=deletable_choices(), value=None),
            False,  # never carry a destructive confirmation into another space
            *refreshed_lists(clear_selections=True),
            "", "", [], "", None, {"answers": {}, "revealed": []}, "", None, "",
        )

    def select_space(space_id):
        try:
            assistant.switch_study_space(space_id)
            return refresh_space()
        except Exception as exc:
            return refresh_space(f"❌ {safe_error(exc)}")

    def create_space(name, space_id):
        try:
            with assistant.operation(space_id):
                assistant.create_study_space(name or "")
            return refresh_space()
        except Exception as exc:
            return refresh_space(f"❌ {safe_error(exc)}")

    def rename_space(name, space_id):
        try:
            with assistant.operation(space_id):
                assistant.rename_study_space(space_id, name or "")
            return refresh_space()
        except Exception as exc:
            return refresh_space(f"❌ {safe_error(exc)}")

    def delete_space(confirmed, target_id, space_id):
        if not confirmed:
            return refresh_space("Check the confirmation box before deleting a Study Space.")
        try:
            with assistant.operation(space_id):
                if not target_id:
                    raise StudySpaceError("Select a Study Space to delete first.")
                assistant.delete_study_space(target_id)
            return refresh_space("Study Space and its stored documents deleted.")
        except Exception as exc:
            return refresh_space(f"❌ {safe_error(exc)}")

    # -- materials -----------------------------------------------------------

    def add_files(files, space_id):
        try:
            with assistant.operation(space_id):
                if not files:
                    return ("<p>Choose one or more files first.</p>", *refreshed_lists())
                messages = []
                for file in files:
                    path = Path(file if isinstance(file, str) else file.name)
                    name = path.name
                    try:
                        result = assistant.library.add_file(path, display_name=name)
                        messages.append("<p>" + ("✅ " if result.added else "ℹ️ ") + html.escape(result.message) + "</p>")
                    except Exception as exc:  # one bad file shouldn't stop the others
                        messages.append(f"<p>❌ {html.escape(name)}: {html.escape(safe_error(exc))}</p>")
                return ("".join(messages), *refreshed_lists())
        except StudySpaceError as exc:
            return (f"<p>❌ {html.escape(safe_error(exc))}</p>", *refreshed_lists())

    def remove_doc(doc_id, space_id):
        try:
            with assistant.operation(space_id):
                if not doc_id:
                    return ("<p>Choose a document to remove.</p>", *refreshed_lists())
                return (f"<p>{html.escape(assistant.library.remove(doc_id))}</p>", *refreshed_lists())
        except StudySpaceError as exc:
            return (f"<p>❌ {html.escape(safe_error(exc))}</p>", *refreshed_lists())

    # -- asking --------------------------------------------------------------

    def ask(question, doc_ids, space_id):
        if not question or not question.strip():
            return "<p>Type a question first.</p>", "", [], ""
        try:
            with assistant.operation(space_id):
                result = assistant.answerer.ask(question, doc_ids or None)
        except Exception as exc:
            return f"<p>❌ {html.escape(safe_error(exc))}</p>", "", [], ""

        answer_text = html.escape(result.answer).replace("\n", "<br>")
        answer_html = f"<p>{answer_text}</p>"
        for warning in result.warnings:
            answer_html += f"<p>⚠️ {html.escape(redact(warning, secrets))}</p>"
        answer_html += f"<p><small>Answered in {result.seconds:.1f} s · {html.escape(result.model)}</small></p>"

        if result.sources:
            lines = ["<h3>Sources</h3>"]
            for source in result.sources:
                mark = "✓ checked" if source.verified else "✗ not confirmed"
                lines.append(f"<p><strong>{html.escape(source.evidence.citation)}</strong> "
                             f"({html.escape(source.evidence.kind)}) · <span class='verified'>{mark}</span></p>")
                if source.quote:
                    lines.append(f"<blockquote>{html.escape(source.quote)}</blockquote>")
            sources_html = "".join(lines)
        else:
            sources_html = "<p><em>No sources: nothing in the materials supported an answer.</em></p>" if not result.found else ""

        seen, gallery = set(), []
        for source in result.sources:
            item = source.evidence
            if item.image_path and (item.doc_id, item.page) not in seen:
                seen.add((item.doc_id, item.page))
                gallery.append((item.image_path, item.citation))

        evidence_lines = []
        for item in result.evidence:
            preview = html.escape(item.text.strip().replace("\n", " ")[:220]) or "(image only)"
            evidence_lines.append(f"<p><strong>{html.escape(item.evidence_id)}</strong> "
                                  f"{html.escape(item.citation)} ({html.escape(item.kind)}, score {item.score:.3f}): {preview}</p>")
        return answer_html, sources_html, gallery, "".join(evidence_lines) or "<p><em>No evidence found.</em></p>"

    # -- quizzes -------------------------------------------------------------

    def make_quiz(doc_ids, topic, count, space_id):
        try:
            with assistant.operation(space_id):
                quiz = assistant.quiz_maker.make_quiz(doc_ids or [], topic or "", int(count))
        except Exception as exc:
            return None, {"answers": {}, "revealed": []}, f"<p>❌ {html.escape(safe_error(exc))}</p>"
        notes = "".join(f"<p>ℹ️ {html.escape(note)}</p>" for note in quiz.notes)
        doc_names = ", ".join(html.escape(name) for name in quiz.doc_names)
        topic_html = f", topic: {html.escape(quiz.topic)}" if quiz.topic else ""
        header = f"<p>Quiz ready: {len(quiz.questions)} questions from {doc_names}{topic_html}</p>"
        return quiz, {"answers": {}, "revealed": []}, header + f"<p><small>Written by {html.escape(quiz.source)}</small></p>" + notes

    with gr.Blocks(title="Course Assistant") as app:
        gr.Markdown("# Course Assistant\nAsk questions about your course materials, see the exact slides behind each answer, and take practice quizzes.")

        with gr.Accordion("Study Spaces", open=True):
            gr.Markdown("Each Study Space has separate documents, slide images and search indexes. This local selector is not a login or security boundary.")
            space_select = gr.Dropdown(choices=space_choices(), value=assistant.study_spaces.active_id, label="Study Space")
            space_status = gr.HTML()
            with gr.Row():
                new_space = gr.Textbox(label="New Study Space", placeholder="e.g. Finance Final")
                create_button = gr.Button("Create", scale=0)
            with gr.Row():
                rename_input = gr.Textbox(label="Rename Study Space")
                rename_button = gr.Button("Rename", scale=0)
            with gr.Accordion("Delete a Study Space", open=False):
                gr.Markdown("Switch to another space first, then select the space to remove. Deletion permanently removes its documents, images and indexes.")
                delete_target = gr.Dropdown(choices=deletable_choices(), label="Study Space to delete")
                delete_confirm = gr.Checkbox(label="Delete Study Space")
                delete_button = gr.Button("Delete permanently", variant="stop")

        with gr.Tab("Materials"):
            gr.Markdown(f"**Accepted files:** {SUPPORTED_DESCRIPTION} Uploading the same file twice won't duplicate it.")
            uploader = gr.File(label="Add course files", file_count="multiple", file_types=sorted(SUPPORTED_TYPES))
            add_button = gr.Button("Add to library", variant="primary")
            add_status = gr.HTML()
            docs = gr.Dataframe(value=doc_table(), headers=["Document", "Type", "Pages", "Text chunks"], interactive=False, label="Library")
            with gr.Row():
                remove_choice = gr.Dropdown(choices=doc_choices(), label="Remove a document", scale=3)
                remove_button = gr.Button("Remove", variant="stop", scale=1)
            with gr.Accordion("AI services in use", open=False):
                gr.HTML(service_status_html())

        with gr.Tab("Ask"):
            ask_docs = gr.Dropdown(choices=doc_choices(), multiselect=True, label="Search in (leave empty to search everything)")
            question = gr.Textbox(label="Question", placeholder="e.g. Find the meme about vibe coding on \"Prod\" and explain it", lines=2)
            ask_button = gr.Button("Ask", variant="primary")
            answer = gr.HTML()
            with gr.Row():
                with gr.Column(scale=1):
                    sources = gr.HTML()
                with gr.Column(scale=1):
                    gallery = gr.Gallery(label="Source slides and pages", columns=1, height=560, object_fit="contain", elem_classes="source-gallery")
            with gr.Accordion("All evidence the model saw", open=False):
                evidence = gr.HTML()

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
                    labels = [f"{LETTERS[i]}. {option}" for i, option in enumerate(item.options)]
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
        space_outputs = [space_select, space_status, delete_target, delete_confirm, *lists, answer, sources, gallery, evidence, quiz_state, progress, quiz_header, uploader, add_status]
        space_select.input(select_space, inputs=space_select, outputs=space_outputs, concurrency_id="study-spaces", concurrency_limit=1)
        create_button.click(create_space, inputs=[new_space, space_select], outputs=space_outputs, concurrency_id="study-spaces", concurrency_limit=1)
        rename_button.click(rename_space, inputs=[rename_input, space_select], outputs=space_outputs, concurrency_id="study-spaces", concurrency_limit=1)
        delete_button.click(delete_space, inputs=[delete_confirm, delete_target, space_select], outputs=space_outputs, concurrency_id="study-spaces", concurrency_limit=1)
        add_button.click(add_files, inputs=[uploader, space_select], outputs=[add_status, *lists], concurrency_id="study-spaces", concurrency_limit=1)
        remove_button.click(remove_doc, inputs=[remove_choice, space_select], outputs=[add_status, *lists], concurrency_id="study-spaces", concurrency_limit=1)
        ask_button.click(ask, inputs=[question, ask_docs, space_select], outputs=[answer, sources, gallery, evidence], concurrency_id="study-spaces", concurrency_limit=1)
        question.submit(ask, inputs=[question, ask_docs, space_select], outputs=[answer, sources, gallery, evidence], concurrency_id="study-spaces", concurrency_limit=1)
        quiz_button.click(make_quiz, inputs=[quiz_docs, topic, count, space_select], outputs=[quiz_state, progress, quiz_header], concurrency_id="study-spaces", concurrency_limit=1)
        app.load(refreshed_lists, outputs=lists)
        app.load(lambda: gr.update(choices=space_choices(), value=assistant.study_spaces.active_id), outputs=space_select)
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
