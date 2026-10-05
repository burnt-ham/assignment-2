"""Unit tests: combining keyword, text and image search, and reranking."""

from course_assistant.retrieval import Retriever, reciprocal_rank_fusion
from course_assistant.services import OverlapReranker
from conftest import FailingReranker


def test_rank_fusion_rewards_items_found_by_several_searches():
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]])
    assert max(scores, key=scores.get) == "b"
    assert set(scores) == {"a", "b", "c", "d"}


def test_hybrid_search_returns_text_and_images_with_ids(make_assistant, sample_pdf):
    assistant = make_assistant()
    assistant.library.add_file(sample_pdf)
    result = assistant.retriever.search("How does reranking prioritize chunks?")
    kinds = {e.kind for e in result.evidence}
    assert kinds == {"text", "image"}
    assert [e.evidence_id for e in result.evidence] == [f"E{i}" for i in range(1, len(result.evidence) + 1)]
    assert result.evidence[0].page == 3
    assert result.reranked


def test_embeddings_only_mode_skips_keyword_search(make_assistant, sample_pdf, monkeypatch):
    assistant = make_assistant(retrieval_mode="embeddings_only")
    assistant.library.add_file(sample_pdf)
    called = []
    monkeypatch.setattr(assistant.library, "keyword_search", lambda *a, **k: called.append(1) or [])
    assistant.retriever.search("chunking")
    assert called == []


def test_reranker_failure_falls_back_with_warning(make_assistant, sample_pdf):
    assistant = make_assistant(reranker=FailingReranker())
    assistant.library.add_file(sample_pdf)
    result = assistant.retriever.search("chunking metadata")
    assert result.evidence and not result.reranked
    assert any("Reranking was skipped" in w for w in result.warnings)


def test_rerank_can_be_turned_off(make_assistant, sample_pdf):
    assistant = make_assistant()
    assistant.library.add_file(sample_pdf)
    retriever = Retriever(assistant.library, OverlapReranker(), use_rerank=False)
    assert not retriever.search("chunking").reranked
