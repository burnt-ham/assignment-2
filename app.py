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
from course_assistant.quiz import LETTERS, MAX_QUESTIONS, QuizError, grade
from course_assistant.services import ServiceError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("course_assistant.app")

CSS = """
.source-gallery img, .quiz-source img { background: #ffffff; border: 1px solid var(--border-color-primary); }
.verified { color: var(--color-accent); font-weight: 600; }
"""


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

    def ask(question, doc_ids, rerank_on):
        if not question or not question.strip():
            return "Type a question first.", "", [], ""
        try:
            result = assistant.answerer.ask(question, doc_ids or None, use_rerank=bool(rerank_on))
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
            return None, {"answers": {}, "revealed": []}, f"❌ {safe_error(exc)}"
        notes = "".join(f"\n> ℹ️ {html.escape(n)}" for n in quiz.notes)
        header = f"Quiz ready: {len(quiz.questions)} questions from {', '.join(quiz.doc_names)}" + (f", topic: {html.escape(quiz.topic)}" if quiz.topic else "")
        return quiz, {"answers": {}, "revealed": []}, header + f"\n\n<sub>Written by {html.escape(quiz.source)}</sub>" + notes

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
            rerank_on = gr.Checkbox(
                value=assistant.settings.use_rerank,
                label="Rerank results with the class multimodal reranker",
                info=("Turn off to compare raw fused search order with reranked order "
                      f"(currently {'on' if assistant.settings.use_rerank else 'off'} by default, USE_RERANK)."),
            )
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
            quiz_header = gr.Markdown()
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
                        gr.Markdown(f"**{index + 1}. {html.escape(item.question)}**")
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
                            if index in answers:
                                verdict = "✅ Correct." if answers[index] == item.correct_index else f"❌ Not quite. The answer is **{item.correct_letter}**."
                            else:
                                verdict = f"The answer is **{item.correct_letter}**."
                            source = item.evidence
                            feedback = f"{verdict} {html.escape(item.explanation)}\n\n**Source:** {html.escape(source.citation)}"
                            if item.quote:
                                feedback += f"\n> {html.escape(item.quote)}"
                            gr.Markdown(feedback)
                            if source.image_path:
                                gr.Image(value=source.image_path, label=source.citation, show_label=True, height=320, elem_classes="quiz-source", interactive=False)

        lists = [docs, remove_choice, ask_docs, quiz_docs]
        add_button.click(add_files, inputs=uploader, outputs=[add_status, *lists])
        remove_button.click(remove_doc, inputs=remove_choice, outputs=[add_status, *lists])
        ask_button.click(ask, inputs=[question, ask_docs, rerank_on], outputs=[answer, sources, gallery, evidence])
        question.submit(ask, inputs=[question, ask_docs, rerank_on], outputs=[answer, sources, gallery, evidence])
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
