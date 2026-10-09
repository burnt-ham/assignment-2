# Course Assistant (MBAX 6418, Assignment 2)

A Python app that answers questions about the course materials and makes practice quizzes. It searches the slides by keyword, by meaning, and by what the slide images look like (hybrid RAG). It shows the actual slide behind every answer, and it says so when the materials don't cover a question.

> **Status: first full version, not yet tested against the class AI services.**
> The app was built and tested in a workspace that can't reach dobolyi.com. Everything below that says "offline" ran with built-in stand-ins instead of the class models. The team still needs to connect the class services, rerun the evaluation, and replace the screenshots and results. See [What was tested and what wasn't](#what-was-tested-and-what-wasnt).

Other project docs: [PLAN.md](PLAN.md) (build plan and issues), [docs/assignment-summary.md](docs/assignment-summary.md), [docs/decisions.md](docs/decisions.md), [docs/course-materials.md](docs/course-materials.md).

## Screenshots

**An answer with its supporting slide image** (offline mode, searching Week 2 for the "Vibe Coding on 'Prod'" meme). The sources list the deck and slide number, each quote is checked against the source, and the original slide image is shown:

![Answer with the source slide image](docs/images/answer-with-slide.png)

**Quiz feedback with sources** (offline mode). The answer stays hidden until you check your answer or ask to see it. Feedback shows the correct option, the source and the original slide:

![Quiz feedback with the source slide](docs/images/quiz-feedback.png)

Dark mode ([screenshot](docs/images/answer-dark-mode.png)) and the Materials tab ([screenshot](docs/images/materials.png)) were also checked.

## Setup

### 1. Install the software

You need:
- **Python 3.11 or newer**: [python.org/downloads](https://www.python.org/downloads/)
- **LibreOffice** (free), to read PowerPoint and Word files: [libreoffice.org/download](https://www.libreoffice.org/download/)
  - On a Mac, LibreOffice installs `soffice` at `/Applications/LibreOffice.app/Contents/MacOS/soffice`. Put that path in `SOFFICE_PATH` in your `.env` (step 3).
  - On Windows it's usually `C:\Program Files\LibreOffice\program\soffice.exe`.
  - Without LibreOffice, PDF, TXT and Markdown still work. Export decks to PDF in PowerPoint and upload the PDF instead.

### 2. Get the code and install the Python packages

```bash
git clone https://github.com/burnt-ham/assignment-2.git
cd assignment-2
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Connect the class services

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` in a text editor and fill in the class key, plus the address and model name of each class service. The file has a comment for each line.

- `.env` is ignored by git, so your key stays on your computer. **Never put a real key in `.env.example`, the README, screenshots or anything else that gets committed.**
- Any service you leave blank uses an offline stand-in, so the app always starts. The Materials tab shows which services are in use under **AI services in use**.
- The chat model's address and name come from the Week 4 slides. The embedding, reranking and document-parsing services are on ports 9001 and up; get their ports and model names from the class.

### 4. Start the app

```bash
python app.py
```

Open http://127.0.0.1:7860 in your browser. To stop it, press Ctrl+C in the terminal.

## Using the app

**Materials tab:** choose one or more files and click **Add to library**. Large decks take a while; the 42-slide Week 2 deck took about 70 seconds to convert. Uploading a file that's already there does nothing, even under a different file name. To remove a document, pick it in **Remove a document** and click **Remove**. Its text, slide images and search entries are all deleted, so later answers can't use it.

**Ask tab:** type a question and click **Ask**. You can limit the search to certain documents. The answer shows:
- the sources, each with its document and slide or page, the quote it relies on, and whether that quote was found in the source (**✓ checked**)
- the original slide or page images
- under **All evidence the model saw**, everything the search found, with scores

If nothing in the materials supports an answer, the app says so instead of guessing.

**Quiz tab:** pick documents, optionally type a topic, choose how many questions, and click **Make quiz**. For each question, pick an answer and click **Check answer**, or click **Show answer** to see it without scoring. The score counts only checked answers. The answer key is fixed when the quiz is made, and the explanation shows the source excerpt and slide.

### Supported files

| Type | How it's read |
|---|---|
| PDF (.pdf) | Text from each page, plus an image of each page |
| PowerPoint (.pptx, .ppt) | Converted to PDF with LibreOffice, then read like a PDF, one image per slide |
| Word (.docx, .doc) | Converted to PDF with LibreOffice, then read like a PDF |
| Text (.txt), Markdown (.md) | Split into sections at Markdown headings; no images |

Speaker notes in PowerPoint files are not read. Repeated headers and footers (like "Syllabus (Subject to Change) 3") are removed from page text so they don't clutter search results. All five course decks in `materials/` were converted and checked by eye, including Week 2 slide 33 (the meme), which matches the original.

## How it works

![Architecture diagram](docs/architecture.svg)

| Step | What happens | Code | Runs on |
|---|---|---|---|
| Document preparation | Upload, skip duplicates (file fingerprint), convert Office files with LibreOffice, extract text and render page images with PyMuPDF, split text into chunks that keep the document and slide number | `course_assistant/ingest.py`, `library.py` | Your computer |
| Indexes | Keyword index (bm25s), text vector index and a separate image vector index (chromadb), all saved in `data/` | `library.py` | Your computer |
| Embeddings | Text chunks go to the text embedding model; page images go to the visual embedding model | `services.py` | Class services |
| Retrieval | Search all three indexes, combine the results with reciprocal rank fusion, then rerank with the multimodal reranker | `retrieval.py` | Your computer + class reranker |
| Answer generation | Send the best text chunks and slide images to the vision chat model with answering rules; it must reply in JSON with separate `answer` and `sources` fields | `answering.py` | Class chat model |
| Checks | Validate the JSON (with one retry), drop citations to evidence that wasn't supplied, check each quote appears in its source, and withhold answers nothing supports | `answering.py` | Your computer |
| Quizzes | Same retrieval, a quiz-writing prompt, then checks for 4 distinct options, one valid correct answer, and a real supporting quote | `quiz.py` | Your computer + class chat model |
| Interface | Gradio app with Materials, Ask and Quiz tabs | `app.py` | Your computer |

**Offline stand-ins** (`services.py`) take over when a service isn't configured or doesn't respond: word-count vectors instead of text embeddings, page text instead of real image embeddings, word overlap instead of the reranker, and quoted passages instead of written answers. Quizzes fall back to fill-in-the-blank questions. The app shows a warning when this happens.

**Keys:** read only from `.env` or environment variables on the computer running the app. They are sent only to the class services, and are removed from any error message before it is shown or logged (`config.py: redact`).

## Tests

```bash
python -m pytest
```

53 automated tests run in about 15 seconds and need no internet or class services. They use fake AI services that return scripted replies.

- **Unit tests** (`tests/test_ingest.py`, `test_library.py`, `test_retrieval.py`, `test_answering.py`, `test_quiz.py`, `test_config.py`) check individual parts: reading PDF, Markdown and PowerPoint files, chunking with source details, duplicate detection, removal, rank fusion, JSON parsing, quote checks, dropping invented citations, quiz validation, scoring, the fixed answer key, and that keys never appear in error messages.
- **End-to-end tests** (`tests/test_end_to_end.py`) run complete workflows: add files, repeat an upload, ask text and image questions, make and score a quiz, remove a document and confirm answers no longer use it, questions the materials can't answer, and the chat model and reranker both being down.

The PowerPoint test is skipped automatically if LibreOffice isn't installed.

## Evaluation

The question set is in [eval/questions.json](eval/questions.json): five text questions (syllabus and slides), two visual questions (the Week 2 meme and the Week 5 hybrid RAG diagram), and one question the materials can't answer.

| ID | Question | Type | Expected source |
|---|---|---|---|
| Q1 | When are the professor's office hours? | text | Syllabus p.1 |
| Q2 | How much of the final grade is the final project worth? | text | Syllabus p.2 |
| Q3 | What is the penalty for turning in an assignment late? | text | Syllabus p.2-3 |
| Q4 | What is KV caching, and what is its tradeoff? | text | Week 2 slide 11 |
| Q5 | In a hybrid RAG pipeline, what does reranking do? | text | Week 5 slide 18 |
| Q6 | Find the meme about Vibe Coding on "Prod" in the Week 2 slides. What do its image and text show? | visual | Week 2 slide 33 |
| Q7 | Describe the diagram of the hybrid RAG pipeline with reranking from the Week 5 slides. | visual | Week 5 slide 18 |
| Q8 | How much does a parking permit for the Koelbel building cost? | not answerable | none |

**Design comparison:** reranking on vs. off, with the same questions, the same files and hybrid search in both settings. The script can also compare hybrid search (keyword + text + image) with embeddings only. To run a comparison, start the app once, add all five decks and the syllabus PDF, stop the app, then run:

```bash
python scripts/evaluate.py --compare rerank   # reranking on vs. off (the comparison reported below)
python scripts/evaluate.py                    # hybrid vs. embeddings only
```

Results are saved in `eval/results/` as JSON, CSV and a Markdown table. The script checks each answer automatically: the expected source was cited, the answer contains an expected keyword, and an unanswerable question was declined. It also records the time taken. Then read every answer and fill in the **Correct (team check)** column yourselves, because the automatic check is only a first pass.

### Results: reranking on vs. off (class services)

We ran the comparison three times on 2026-10-08 with all five class services connected. The full tables and per-question notes are in [eval/results/live-rerank-summary.md](eval/results/live-rerank-summary.md). Junkyu graded every answer from run 3 by hand against its expected answer. The **correct** column asks whether the answer is right. The **sources** column asks whether every cited source supports it.

| Setting | Automatic check (runs 1-3) | Team check, run 3: correct | Team check, run 3: sources | Average time per question |
|---|---|---|---|---|
| Reranking on | 8/8, 8/8, 8/8 | 7/8 | 6/8 | 5.3 s |
| Reranking off | 8/8, 8/8, 8/8 | 8/8 | 7/8 | 2.1 s |

Where the team check differed from the automatic check:
- **Extra, unrelated citations.** Reranking on: Q2 (syllabus p.7) and Q5 (Week 5 slide 14). Reranking off: Q7 (Week 5 slide 5). Each answer was right and also cited the correct source, so the automatic check, which only looks for the expected source, couldn't catch these.
- **An invented image detail.** With reranking on, the Q6 meme answer said Boromir has "his arms outstretched". In the image he raises one hand with his fingers together.

**Interpretation.** We would keep reranking **off** by default. On our questions it didn't make any answer more accurate or any citation better, and it made every answer slower, about 2.5 times on average. The time cost showed up in every question and every run. The one-question differences in correctness and sources could be chance, because answers vary from run to run and we graded only one run by hand. So we don't conclude that reranking hurts. We conclude that it didn't help here. Our questions each have one clearly matching slide, and the expected source was retrieved in every run without reranking. Reranking would matter more when many similar passages compete, such as broad questions that span several decks. That's why we kept it as a setting (`USE_RERANK=true` in `.env`) instead of removing it.

**Limits of this comparison:** only 8 questions, mostly with a single clear source; hand grading of one run out of three; and answers that change between runs.

## Limitations and an investigated failure

**Investigated: empty and off-topic answers from the class chat model.** In our first evaluation run with the class services, reranking on scored 7/8 and off scored 8/8. That looked like evidence against reranking, but the miss came from the answer step, not from retrieval:
1. On Q3 the app said "Sorry, I couldn't produce a valid answer". Calling the chat model directly showed `finish_reason: length` with an empty reply. The model (Qwen3) had spent its whole token budget reasoning before writing its answer. Raising the limit from 2000 to 6000 tokens didn't help. Turning reasoning off (`enable_thinking: false`) did: the model answered in 26 tokens.
2. With reasoning off, Q3 and Q4 were answered as if the question were "what is a prompt", taken from text in the evidence. This happened in 6 of 6 runs. The question appeared only once, at the top of a long message followed by the evidence and slide images. Repeating the question after the images fixed it in 6 of 6 runs.

After both fixes, both settings scored 8/8 on the automatic check in three runs, and answers got faster. The lesson for the comparison: a score difference between two settings can come from a bug that has nothing to do with the setting being compared.

**Investigated: the meme question without a document filter (offline).** Asking "Find the meme about Vibe Coding on 'Prod' in the Week 2 slides" across all documents returned syllabus page 7 first and the meme slide second. The syllabus schedule mentions "Week 2", "Vibe Coding" and other query words, so word-overlap scoring ranked it higher. The meme slide itself has only its title as text. Limiting the search to the Week 2 deck put slide 33 first (see the screenshot). With the class visual embedding model and reranker, the image itself should be matched, so the team should rerun this to confirm.

Other limitations:
- **Image descriptions aren't checked automatically.** The chat model sometimes adds details that aren't in the picture (see Q6 above), and how much it describes varies from run to run. The slide image is shown next to the answer so you can compare.
- **Service errors.** If a class service replies with an error, the message appears in the app and the stand-in takes over for that question.
- **Source checking is by quote only.** The app confirms each quoted phrase appears in its source. It doesn't check that the source fully supports every sentence of the answer. For image evidence it can't check automatically, so the slide is shown for you to compare.
- **Picture-only slides:** without the document parser service, a slide whose only text is in an image can be found only through image search.
- **Speed:** converting large decks takes time (Week 2: about 70 s; Week 6, which has embedded videos, about 28 s). Videos and animations aren't captured, only a still image of each slide.
- **One user at a time:** the library is shared by everyone using the same running app.

## What was tested and what wasn't

| Tested | How |
|---|---|
| All 53 automated tests pass | `python -m pytest`, Python 3.11, Linux. The PowerPoint conversion test needs a LibreOffice install that can open PPTX files |
| All five course decks and the syllabus convert and load | Loaded in the app; slide images compared with the originals by eye |
| Add, repeat upload, remove, ask, quiz, check and show answer | Clicked through in a browser (Chromium) in offline mode |
| Light and dark mode are readable | Screenshots of every tab in both modes |
| Keys stay out of errors | Unit test with a fake service that echoes the key back |
| Evaluation script runs | Offline dry run, saved in `eval/results/` |
| All five class services, the Ask tab and the evaluation questions with the real models | On Windows, 2026-10-08: all five services answered, the five decks and the syllabus loaded with LibreOffice, and the 8 questions plus 3 reranking on/off runs completed (see [Evaluation](#evaluation)) |
| Answer quality with the real chat model | Run 3 answers graded by hand ([eval/results/live-rerank-summary.md](eval/results/live-rerank-summary.md)) |
| Quizzes with the real chat model | 3 quizzes (15 questions) checked against the slides and syllabus: every question answerable, every key correct, answer key fixed across regrading, solutions hidden until checked or shown ([eval/results/live-quiz-check.md](eval/results/live-quiz-check.md)) |

| Not tested yet | Why |
|---|---|
| Hybrid vs. embeddings-only comparison with the class services | We compared reranking on vs. off instead |
| Setup on Mac, and a fresh-clone setup by a teammate | A teammate should follow this README on a fresh clone and fix any missing steps |

## Working on this as a team

- **Branch:** your own copy of the code to work on without affecting anyone else. Create one from `main` for each task, e.g. `git checkout -b fix-quiz-scores`.
- **Commit:** a saved checkpoint of your changes with a short message.
- **Pull request (PR):** a request to merge your branch into `main`. A teammate reviews it on GitHub before it's merged.
- **Issue:** a task on GitHub. The plan's steps are issues #2 to #12; assign yourself to the ones you pick up.
