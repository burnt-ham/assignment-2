# Assignment 2 Summary: Build a Course Assistant

MBAX 6418, Fall 2026. The full text is in [source-materials/assignment-2.pdf](source-materials/assignment-2.pdf).

## What the app must do
- **Manage materials:** users add and remove documents in the app. Loading the same file twice must not duplicate it. Removing a document also removes its searchable content. Direct PPTX upload is preferred (automatic conversion with LibreOffice); exporting to PDF by hand is an acceptable fallback. Document the supported formats and any conversion steps.
- **Answer questions** from the materials, including diagrams and charts. Say when information is missing, and never invent answers or citations.
- **Show visual evidence:** retrieve relevant slide images through RAG and show them next to the answer, with the document name and slide number.
  - Required test: find the "Vibe Coding on 'Prod'" meme in the Week 2 slides (slide 33, which is image only), then summarize what its image and text show.
- **Quizzes:** the user picks course material and an optional topic. Questions are multiple choice, with scores and explanations. The answer key is fixed, and solutions stay hidden until the user answers or asks. Feedback cites its sources.
- **Citations:** every answer and every piece of quiz feedback shows the document and page, slide or section, plus a real source excerpt or screenshot.

## How it must be built
- Python app and interface (the team chose Gradio).
- Hybrid RAG: keyword search plus text embeddings plus visual embeddings. The text and visual indexes stay separate, their results are combined and reranked, and the best evidence goes to the model, including images.
- Structured output with separate `answer` and `sources` fields. Validate the format and check that the sources support the answer.
- Suggested packages: bm25s, langchain-text-splitters, chromadb, gradio.
- Class endpoints at dobolyi.com, ports 9001 and up: vision LLM, text and visual embeddings, multimodal reranker, document parser.
- Keys stay server-side, in environment variables or a local file. They must never appear in the UI, logs, errors, screenshots, docs, test results, or GitHub. Config examples use dummy values.

## Testing
- Unit tests and end-to-end tests. Cover adding and removing files, repeat uploads, text and image questions, generating and scoring quizzes, missing information, and model services being unavailable.
- Check that quiz questions can be answered from the selected material, that scores match the answer key, and that the sources support the explanations.
- Check light and dark modes for readability.

## Evaluation
- Write 5-10 questions covering the syllabus and slide text. Include at least two visual questions (one is the meme) and one question the materials can't answer.
- Compare two approaches to one part of the RAG system using the same questions and files. Record whether each answer is correct, whether its sources support it, and how long it takes. Interpret the results and say which approach to keep.

## Deliverables (README.md is both the report and the docs)
- Setup and use: install, launch, endpoint config, adding and removing documents, asking questions, quizzes, rerunning tests, supported formats, known limitations.
- An SVG architecture diagram saved in the repo and embedded in the README. It must show document preparation, keyword, text and image retrieval, reranking, and answer generation, and it must match the final app.
- Results and findings: the question set, the comparison results, the interpretation, and at least one limitation or failure that was investigated.
- At least two screenshots: an answer with a supporting slide image, and quiz feedback with sources.
- A clear statement of what was tested and what remains unchecked.

## Teamwork and submission
- One shared repo. Use issues for substantial tasks and PRs reviewed before merging. Everyone contributes meaningfully and reviews a teammate's work.
- Submit one repo link per team on Canvas, and give the instructor access. The most recent commit before the deadline is graded.
- A teammate should be able to deploy the app from a fresh clone by following the README.
