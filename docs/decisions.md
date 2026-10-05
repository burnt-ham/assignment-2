# Team Decisions

Decisions from Christopher (burnt-ham) in the project thread on 2026-10-05.

| Topic | Decision |
|---|---|
| Who builds | Claude builds the full app; the team then tests, fixes bugs and refines on their own branches. |
| Interface | Gradio |
| PowerPoint | Automatic conversion with LibreOffice (PPTX to PDF to page images) |
| Design comparison | Default: hybrid search vs. embeddings-only search (`scripts/evaluate.py`). Reranking on vs. off is also available. |
| AI services | The app uses the class services at dobolyi.com (chosen over the Claude API, 2026-10-05). Any service left unconfigured uses an offline stand-in. Claude's workspace can't reach dobolyi.com, so real-service testing happens on the team's computers. |
| Due date | Oct 16, 2026, per Christopher. The syllabus schedule (page 7) lists Oct 5 (Thursday section) and Oct 12 (Monday section), so confirm on Canvas. |
| Sources | Use only the files Christopher provides: the assignment PDF, the syllabus, and the Week 2-6 slide decks. No outside sources. |
| GitHub | Plans and project context go on GitHub as they are made, on `main`. Teammates create their own branches from `main`. |
| Slide decks | All five decks are committed in `materials/`, with the class API key removed from Week 4. |
