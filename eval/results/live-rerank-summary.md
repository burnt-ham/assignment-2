# Reranking on vs. off with the class services

Run on 2026-10-08 on a Windows laptop with all five class services connected (chat model, text embeddings, visual embeddings, reranker, document parser). The library held the syllabus and the Week 2-6 decks. The code was branch `claude/assignment-review-access-d0b8wj` at commit 4a7702d. The command was `python scripts/evaluate.py --compare rerank`, run three times.

| Run | Results file | rerank_on: correct (auto) / average seconds | rerank_off: correct (auto) / average seconds |
|---|---|---|---|
| 1 | [20261008-201150-rerank.md](20261008-201150-rerank.md) | 8/8 / 5.0 s | 8/8 / 2.1 s |
| 2 | [20261008-201248-rerank.md](20261008-201248-rerank.md) | 8/8 / 4.9 s | 8/8 / 2.0 s |
| 3 | [20261008-201355-rerank.md](20261008-201355-rerank.md) ([answers](20261008-201355-rerank.json)) | 8/8 / 5.9 s | 8/8 / 2.1 s |
| **Average** | | **8/8 / 5.3 s** | **8/8 / 2.1 s** |

"Correct (auto)" is only the script's automatic check. Runs 1 and 2 saved only these tables. Run 3 also saved every answer, which the team graded by hand below.

## Team check (run 3)

Junkyu read every run 3 answer in [20261008-201355-rerank.json](20261008-201355-rerank.json) and compared it with the question's `expected_answer`, the syllabus and the slides. Each answer gets two separate grades:
- **Correct:** is the answer right? Possible grades are yes, partial or no.
- **Sources:** does every cited source support the answer?

| Question | rerank_on: correct / sources | rerank_off: correct / sources | Notes |
|---|---|---|---|
| Q1: Office hours | yes / yes | yes / yes | |
| Q2: Final project weight | yes / partial | yes / yes | On: also cites syllabus p.7, the course schedule, which doesn't mention the 45% weight. |
| Q3: Late penalty | yes / yes | yes / yes | On: adds that the lowest-scoring assignment is dropped. That's true (syllabus p.3) but it doesn't answer the question. |
| Q4: KV caching | yes / yes | yes / yes | |
| Q5: What reranking does | yes / partial | yes / yes | On: also cites Week 5 slide 14, a section title slide ("Building a RAG System") that doesn't support the answer. |
| Q6: Vibe Coding meme | partial / yes | yes / yes | On: says Boromir is shown "with his arms outstretched". In the image he raises one hand with his fingers together, so this detail is invented. Neither setting explains what the meme means. |
| Q7: Hybrid RAG diagram | yes / yes | yes / partial | Off: also cites Week 5 slide 5, a title slide ("But What Is Context Engineering?") that has nothing to do with the diagram. Both answers describe the diagram's blue retrieval and orange reranking stages correctly. |
| Q8: Parking permit (unanswerable) | yes / yes | yes / yes | Both say the materials don't cover it, and neither cites a source. |
| **Total** | **7/8 correct, 6/8 sources** | **8/8 correct, 7/8 sources** | |

Every source problem the team found was an extra citation. In each case at least one cited source correctly supported the answer, which is why the automatic check, which only looks for the expected source, missed them.

## Things to keep in mind when reading these results
- The first run with the class services, before commit 4a7702d, scored 7/8 with reranking on and 8/8 with it off. The rerank_on miss on Q3 was an empty reply from the chat model, which spent its whole token budget reasoning. Retrieval was not the cause. That bug is fixed, so those results are not included here.
- Answers vary between runs. For example, Q6 sometimes names Boromir and sometimes only quotes the meme text.
- Some answers cite more sources than they need. In run 3 with reranking on, Q2 also cites syllabus page 7 and Q5 also cites Week 5 slide 14. With reranking off, Q7 also cites Week 5 slide 5.
