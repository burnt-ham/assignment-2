# Reranking on vs. off with the class services

Run on 2026-10-08 on a Windows laptop with all five class services connected (chat model, text embeddings, visual embeddings, reranker, document parser). The library held the syllabus and the Week 2-6 decks. The code was branch `claude/assignment-review-access-d0b8wj` at commit 4a7702d. The command was `python scripts/evaluate.py --compare rerank`, run three times.

| Run | Results file | rerank_on: correct (auto) / average seconds | rerank_off: correct (auto) / average seconds |
|---|---|---|---|
| 1 | [20261008-201150-rerank.md](20261008-201150-rerank.md) | 8/8 / 5.0 s | 8/8 / 2.1 s |
| 2 | [20261008-201248-rerank.md](20261008-201248-rerank.md) | 8/8 / 4.9 s | 8/8 / 2.0 s |
| 3 | [20261008-201355-rerank.md](20261008-201355-rerank.md) ([answers](20261008-201355-rerank.json)) | 8/8 / 5.9 s | 8/8 / 2.1 s |
| **Average** | | **8/8 / 5.3 s** | **8/8 / 2.1 s** |

"Correct (auto)" is the script's automatic check only. The team still has to read the answers and fill in the "Correct (team check)" column. Run 3's JSON file has every answer with its expected answer and cited sources.

Things to keep in mind when reading these results:
- The first run with the class services, before commit 4a7702d, scored 7/8 with reranking on and 8/8 with it off. The rerank_on miss on Q3 was an empty reply from the chat model, which spent its whole token budget reasoning. Retrieval was not the cause. That bug is fixed, so those results are not included here.
- Answers vary between runs. For example, Q6 sometimes names Boromir and sometimes only quotes the meme text.
- Some answers cite more sources than they need. In run 3 with reranking on, Q2 also cites syllabus page 7 and Q5 also cites Week 5 slide 14. With reranking off, Q7 also cites Week 5 slide 5.
