# Assignment 2 Plan: Course Assistant (draft for approval)

Due: Oct 16 per Christopher (the syllabus says Oct 5 or Oct 12, so confirm on Canvas).
Sources: only the files Christopher provides (assignment PDF, syllabus, Week 2-6 decks). See [docs/assignment-summary.md](docs/assignment-summary.md), [docs/decisions.md](docs/decisions.md) and [docs/course-materials.md](docs/course-materials.md).

Each step below is tracked as a GitHub issue.

## Stack
- Python + Gradio, with three tabs: Materials, Ask, Quiz
- bm25s for keyword search, langchain-text-splitters for chunking, chromadb for the vector indexes
- LibreOffice converts PPTX to PDF, and PyMuPDF renders each page or slide to an image
- Class endpoints at dobolyi.com (vision LLM, text and visual embeddings, reranker, document parser), called through the OpenAI-compatible client
- Keys live in a local `.env` file that git ignores. The repo gets a `.env.example` with dummy values only.

## Steps (each one becomes a GitHub issue with a completion check, built on its own branch and merged with a PR)
0. ([#2](https://github.com/burnt-ham/assignment-2/issues/2)) **Discover services.** List the models on the class endpoints and record their request formats. This needs no code in the repo.
1. ([#3](https://github.com/burnt-ham/assignment-2/issues/3)) **Skeleton.** Set up the project layout, config loading, `.gitignore`, the test runner, and a README stub.
2. ([#4](https://github.com/burnt-ham/assignment-2/issues/4)) **Materials.** Upload PDF, PPTX, TXT and MD files. Skip duplicates by file hash. Save the original page and slide images. Parse the text and split it into chunks that keep the document, page or slide, and section. Removing a document removes everything in the indexes that came from it.
3. ([#5](https://github.com/burnt-ham/assignment-2/issues/5)) **Indexes.** Build three separate indexes: keyword (BM25), text embeddings, and slide-image embeddings.
4. ([#6](https://github.com/burnt-ham/assignment-2/issues/6)) **Retrieval.** Run all three searches, merge the results, then rerank with the multimodal reranker.
5. ([#7](https://github.com/burnt-ham/assignment-2/issues/7)) **Answers.** Send the vision LLM the question, the top text chunks, and the slide images. It returns `{answer, sources}` JSON, which the app validates. The app also checks that each source supports the answer. When the materials don't cover the question, the answer says so instead of guessing.
6. ([#8](https://github.com/burnt-ham/assignment-2/issues/8)) **Quizzes.** Pick documents, with an optional topic filter, to generate multiple-choice questions. The answer key is fixed when the quiz is created. Solutions stay hidden until the student answers or asks. The app shows the score and explains each answer with its source excerpt or slide image.
7. ([#9](https://github.com/burnt-ham/assignment-2/issues/9)) **UI polish.** Show source slide images with deck name and slide number. Check light and dark mode.
8. ([#10](https://github.com/burnt-ham/assignment-2/issues/10)) **Tests.** Unit tests, plus end-to-end tests that cover adding and removing files, repeat uploads, text and image questions, quizzes, missing information, and services being down. The tests use stand-in services so they run without the class endpoints.
9. ([#11](https://github.com/burnt-ham/assignment-2/issues/11)) **Evaluation.** Write a 5-10 question set that includes the Week 2 "Vibe Coding on 'Prod'" meme, one more visual question, and one question the materials can't answer. Run the design comparison. Record correctness, whether the sources support each answer, and the time each answer takes.
10. ([#12](https://github.com/burnt-ham/assignment-2/issues/12)) **Docs.** README with setup and usage, an SVG architecture diagram, two or more screenshots, findings and limitations, and a list of what was and wasn't tested.

## Open item
- Which design choice to compare. It isn't needed until step 9. The default is hybrid search vs. embeddings-only search.
