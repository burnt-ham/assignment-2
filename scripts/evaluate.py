"""Run the evaluation questions under two search settings and save the results.

Usage (from the project folder, with the course files already added in the app):
    python scripts/evaluate.py                 # hybrid search vs embeddings only
    python scripts/evaluate.py --compare rerank  # reranking on vs off

Results go to eval/results/: a JSON file with every answer, and a Markdown
table to paste into the README. Automatic checks are a first pass only. Read
the answers and fill in the "correct (team check)" column yourselves, comparing
each answer with its "expected_answer" (copied into the JSON and CSV rows).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from course_assistant.assistant import CourseAssistant  # noqa: E402

COMPARISONS = {
    "retrieval": [("hybrid", {"mode": "hybrid"}), ("embeddings_only", {"mode": "embeddings_only"})],
    "rerank": [("rerank_on", {"use_rerank": True}), ("rerank_off", {"use_rerank": False})],
}


def source_hit(expected: list[dict], cited: list[tuple[str, int]]) -> bool:
    return any(e["document"].lower() in name.lower() and e["page"] == page for e in expected for name, page in cited)


def run(assistant: CourseAssistant, questions: list[dict], label: str, options: dict) -> list[dict]:
    retriever = assistant.retriever
    saved = (retriever.mode, retriever.use_rerank)
    retriever.mode = options.get("mode", retriever.mode)
    retriever.use_rerank = options.get("use_rerank", retriever.use_rerank)
    rows = []
    try:
        for item in questions:
            started = time.perf_counter()
            result = assistant.answerer.ask(item["question"])
            seconds = time.perf_counter() - started
            cited = [(s.evidence.doc_name, s.evidence.page) for s in result.sources]
            retrieved = [(e.doc_name, e.page) for e in result.evidence]
            text = result.answer.lower()
            keywords_ok = any(k in text for k in item["expected_keywords"]) if item["expected_keywords"] else None
            if item["type"] == "unanswerable":
                auto_correct = not result.found
                supported = not result.sources
            else:
                supported = source_hit(item["expected_sources"], cited)
                auto_correct = result.found and supported and (keywords_ok is not False)
            rows.append(
                {
                    "setting": label,
                    "id": item["id"],
                    "type": item["type"],
                    "question": item["question"],
                    "expected_answer": item.get("expected_answer", ""),
                    "answer": result.answer,
                    "found": result.found,
                    "cited_sources": [f"{n}, p.{p}" for n, p in cited],
                    "expected_source_retrieved": source_hit(item["expected_sources"], retrieved) if item["expected_sources"] else None,
                    "sources_support_answer": supported,
                    "auto_correct": auto_correct,
                    "seconds": round(seconds, 2),
                    "model": result.model,
                    "warnings": result.warnings,
                }
            )
            print(f"[{label}] {item['id']}: auto_correct={auto_correct} supported={supported} {seconds:.1f}s", flush=True)
    finally:
        retriever.mode, retriever.use_rerank = saved
    return rows


def summarize(rows: list[dict], labels: list[str]) -> str:
    lines = [
        "| Question | Type | " + " | ".join(f"{l}: correct (auto) / sources support / seconds" for l in labels) + " | Correct (team check) |",
        "|---|---|" + "---|" * len(labels) + "---|",
    ]
    by_key = {(r["setting"], r["id"]): r for r in rows}
    for qid in sorted({r["id"] for r in rows}, key=lambda q: int(q[1:])):
        first = by_key[(labels[0], qid)]
        cells = []
        for label in labels:
            r = by_key[(label, qid)]
            cells.append(f"{'yes' if r['auto_correct'] else 'no'} / {'yes' if r['sources_support_answer'] else 'no'} / {r['seconds']}")
        lines.append(f"| {qid}: {first['question']} | {first['type']} | " + " | ".join(cells) + " | |")
    lines.append("")
    for label in labels:
        subset = [r for r in rows if r["setting"] == label]
        correct = sum(r["auto_correct"] for r in subset)
        supported = sum(bool(r["sources_support_answer"]) for r in subset)
        avg = sum(r["seconds"] for r in subset) / len(subset)
        lines.append(f"- **{label}**: {correct}/{len(subset)} correct (automatic check), {supported}/{len(subset)} with supporting sources, {avg:.1f} s average")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--compare", choices=sorted(COMPARISONS), default="retrieval")
    parser.add_argument("--questions", default=str(ROOT / "eval" / "questions.json"))
    args = parser.parse_args()

    assistant = CourseAssistant()
    if not assistant.library.list_documents():
        sys.exit("The library is empty. Start the app, add the course files on the Materials tab, then run this again.")
    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    settings = COMPARISONS[args.compare]
    rows: list[dict] = []
    for label, options in settings:
        rows.extend(run(assistant, questions, label, options))

    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = out_dir / f"{stamp}-{args.compare}"
    meta = {"comparison": args.compare, "services": assistant.services.status, "documents": [d.name for d in assistant.library.list_documents()]}
    base.with_suffix(".json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=2), encoding="utf-8")
    with open(base.with_suffix(".csv"), "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*rows[0].keys(), "correct_team_check"])
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "cited_sources": "; ".join(row["cited_sources"]), "warnings": "; ".join(row["warnings"]), "correct_team_check": ""})
    table = summarize(rows, [label for label, _ in settings])
    base.with_suffix(".md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)
    print(f"\nSaved {base.with_suffix('.json').name}, .csv and .md in eval/results/")


if __name__ == "__main__":
    main()
