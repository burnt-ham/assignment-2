# Quiz check with the class services

Run on 2026-10-09 on a Windows laptop with all five class services connected. The code was branch `claude/assignment-review-access-d0b8wj` at commit 9ddf4eb, with no changes to the quiz code. The library held the syllabus and the Week 2-6 decks. Quizzes were made with `QuizMaker.make_quiz()`, the same code the Quiz tab uses.

**Scope:** this check covers the quiz code on `main` (with #17), not Quiz 2.0 (#15/#16). The table below lists each question's topic, key and cited source, not the full question and option text. The full generated quizzes are in [live-quiz-check.json](live-quiz-check.json): every question, its four options, the correct letter, the explanation, the cited source and quote, and the answer-key checks. In quiz 3, question 1, the course number appears as "[hidden]". The test script removes the class key from everything it saves, and the key happens to be the same as the course number.

## Quizzes generated

| Quiz | Material | Topic | Written by | Time |
|---|---|---|---|---|
| 1 | Week 2 deck | KV caching | class chat model (Qwen3.6-35B-A3B) | 7.6 s |
| 2 | Week 5 deck | reranking | class chat model | 6.9 s |
| 3 | Syllabus | (none) | class chat model | 4.1 s |

All three quizzes came from the chat model, with no fallback to the offline generator and no warnings.

## Are the questions answerable, and is the key right?

Junkyu checked all 15 questions against the slides and the syllabus. Each question can be answered from the selected material, the marked answer is correct, and the quoted source text appears on the cited slide or page.

| Quiz | Question | Key | Cited source |
|---|---|---|---|
| 1 | Main benefit of a KV cache during decoding | B | Week 2 slide 11 |
| 1 | Tradeoff of KV caching | C | Week 2 slide 11 |
| 1 | How prompt (prefix) caching extends KV caching | A | Week 2 slide 12 |
| 1 | Tokens processed for the eleventh message with prefix caching | C | Week 2 slide 12 |
| 1 | What happens without prefix caching in the example | A | Week 2 slide 12 |
| 2 | Main function of the reranker | C | Week 5 slide 18 |
| 2 | Two search methods combined in hybrid retrieval | A | Week 5 slide 18 |
| 2 | Benefit of retrieving relevant evidence early | B | Week 5 slide 20 |
| 2 | Output of the reranker | B | Week 5 slide 18 |
| 2 | How RAG optimizes context | A | Week 5 slide 13 |
| 3 | When the class meets | A | Syllabus p.1 |
| 3 | Weight of the final project | C | Syllabus p.2 |
| 3 | Late quiz submissions | C | Syllabus p.3 |
| 3 | Cell phone policy | B | Syllabus p.3 |
| 3 | Use of generative AI tools | B | Syllabus p.6 |

**Topic filter:** the questions stay close to the topic, but some drift to nearby content. In quiz 2 (topic "reranking"), questions 3 and 5 are about RAG in general (Week 5 slides 20 and 13). In quiz 1 (topic "KV caching"), questions 3-5 are about prompt caching, which builds on KV caching (Week 2 slide 12). These questions aren't wrong, just broader than the topic.

## Fixed answer key

Quiz 1 had the key B, C, A, C, A. The test graded it twice with every answer correct and twice with every answer wrong. The scores were 5/5 and 0/5 both times, and `key_fingerprint` was the same before and after (`fd144022d1`).

## Quiz tab in the browser

The test used the Week 5 deck, topic "reranking", 3 questions:
- **Before answering:** the score read "0 of 3 correct (0 answered)". No answer, explanation or source was visible. ([screenshot](../../docs/images/quiz-before-live.png))
- **Check answer, right answer:** a green check, the explanation, the source (Week 5 slide 18), the quoted text and the slide image appeared.
- **Check answer, wrong answer:** "Not quite. The answer is A", with the explanation, source and slide image.
- **Show answer without answering:** revealed the answer and its source. It didn't count toward the score.
- **Final score:** "1 of 3 correct (2 answered)". Light and dark mode were both readable. ([light](../../docs/images/quiz-feedback-live.png), [dark](../../docs/images/quiz-feedback-dark-live.png))

## Other observations

- **Switching themes clears the quiz.** Changing `?__theme=light` to `?__theme=dark` reloads the page, so the quiz in progress is lost. The dark screenshot shows a new quiz made with the same settings.
- **The slide label covers the slide title.** The "Week 5 ... slide 18" label sits on top of the slide image and hides the start of the slide's title.
- **The first "Make quiz" attempt failed.** It said "Choose at least one document for the quiz." because the document selection hadn't registered. A follow-up test (offline, Chromium driven by Playwright) found this is not an app bug. These three ways of picking a document all worked: clicking the option in the list, typing part of the name and pressing Enter, and clicking "Make quiz" straight after selecting. The error came back only when text was typed into the box without choosing an option from the list. In that case no document chip appears, so a person can see that nothing is selected.
