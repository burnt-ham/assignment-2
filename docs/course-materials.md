# Course Materials

These are the files the app is built and tested with. Only these sources are used.

| File | Location | Notes |
|---|---|---|
| Assignment 2 PDF | [docs/source-materials/assignment-2.pdf](source-materials/assignment-2.pdf) | 4 pages |
| Syllabus, Fall 2026 | [docs/source-materials/syllabus-fall-2026.pdf](source-materials/syllabus-fall-2026.pdf) | 7 pages; schedule on page 7 |
| Week 2: LLM Fundamentals | `materials/Week 2 - LLM Fundamentals.pptx` | 42 slides, 32 MB. Slide 33 is the "Vibe Coding on 'Prod'" meme. |
| Week 3: Prompt Engineering | `materials/Week 3 - Prompt Engineering.pptx` | 33 slides, 3.5 MB |
| Week 4: Serving and Debugging | `materials/Week 4 - Serving and Debugging.pptx` | 26 slides, 9.8 MB. Class API key removed (see below). |
| Week 5: Context Engineering and RAG | `materials/Week 5 - Context Engineering and RAG.pptx` | 21 slides, 15 MB |
| Week 6: Multimodal Generative AI | `materials/Week 6 - Multimodal Generative AI.pptx` | 20 slides, 56 MB |

## Keeping keys out of the decks
The original Week 4 deck showed the class API key on slides 12-13 and in slide 13's speaker notes. The committed copy has "removed" in all three places. All five decks were scanned for keys (text, speaker notes and metadata), and none remain. The 17 images in the Week 4 deck were not checked, because only text can be searched.

Before committing any new course file, check it for keys. Never commit a real key.

## Large files
The Week 6 deck (56 MB) is over GitHub's 50 MB warning size but under its 100 MB limit, so a plain git push works.
