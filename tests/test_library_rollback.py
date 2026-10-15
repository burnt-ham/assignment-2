"""Failure injection for document-removal rollback."""

import pytest
from pathlib import Path
from gradio import processing_utils
from conftest import make_pdf
from app import remove_document

from course_assistant.library import Library
from course_assistant import library as library_module
from course_assistant.services import HashingTextEmbedder, PageTextImageEmbedder


def test_failed_keyword_rebuild_restores_both_vector_indexes(sample_pdf, tmp_path, monkeypatch):
    library = Library(tmp_path / "data", HashingTextEmbedder(), PageTextImageEmbedder())
    doc = library.add_file(sample_pdf).document
    previous_keyword = library._bm25
    original_rebuild = library._rebuild_keyword_index
    monkeypatch.setattr(library, "_rebuild_keyword_index", lambda: (_ for _ in ()).throw(OSError("index offline")))
    with pytest.raises(OSError, match="index offline"):
        library.remove(doc.doc_id)
    monkeypatch.setattr(library, "_rebuild_keyword_index", original_rebuild)
    assert library._bm25 is previous_keyword
    assert library.keyword_search("reranking", 5)
    assert doc.doc_id in library.documents
    assert (library.docs_dir / doc.doc_id).exists()
    assert library.image_index.count() == len(doc.pages)
    assert library.text_index.count() == len(doc.chunks)
    library.close()
    reopened = Library(tmp_path / "data", HashingTextEmbedder(), PageTextImageEmbedder())
    assert doc.doc_id in reopened.documents


def test_removing_document_keeps_shared_slide_cache_for_remaining_document(tmp_path, monkeypatch):
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio-cache"))
    library = Library(tmp_path / "data", HashingTextEmbedder(), PageTextImageEmbedder())
    shared_page = "The shared lecture page has the same words and visual layout."
    first = library.add_file(make_pdf(tmp_path / "first.pdf", [shared_page, "First document only"])).document
    second = library.add_file(make_pdf(tmp_path / "second.pdf", [shared_page, "Second document only"])).document
    assert processing_utils.hash_file(first.pages[0].image_path) == processing_utils.hash_file(second.pages[0].image_path)
    cached = processing_utils.save_file_to_cache(first.pages[0].image_path, str(tmp_path / "gradio-cache"))
    assert "Removed" in remove_document(library, first.doc_id)
    assert Path(cached).exists()
    assert second.doc_id in library.documents


def test_postcommit_source_cleanup_failure_still_removes_unreferenced_gradio_copy(sample_pdf, tmp_path, monkeypatch):
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio-cache"))
    library = Library(tmp_path / "data", HashingTextEmbedder(), PageTextImageEmbedder())
    doc = library.add_file(sample_pdf).document
    cached = processing_utils.save_file_to_cache(doc.pages[0].image_path, str(tmp_path / "gradio-cache"))
    original = library_module._remove_tree

    def fail_source_cleanup(path, *args, **kwargs):
        if path == library.docs_dir / doc.doc_id:
            raise OSError("locked source")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(library_module, "_remove_tree", fail_source_cleanup)
    with pytest.raises(OSError, match="files remain"):
        remove_document(library, doc.doc_id)
    assert doc.doc_id not in library.documents
    assert not Path(cached).exists()
    assert (library.docs_dir / doc.doc_id).exists()  # failure must not be hidden
