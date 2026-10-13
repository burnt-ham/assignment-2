"""Unit tests: adding, de-duplicating and removing documents in the library."""

import shutil
from pathlib import Path

import pymupdf
from conftest import make_pdf

from course_assistant.library import Library, chunk_pages, same_content
from course_assistant.ingest import Page, file_hash
from course_assistant.services import HashingTextEmbedder, PageTextImageEmbedder


def new_library(path):
    return Library(path, HashingTextEmbedder(), PageTextImageEmbedder())


def test_chunks_keep_source_details():
    pages = [Page(4, "slide", "word " * 400, "Long slide"), Page(5, "slide", "", "")]
    chunks = chunk_pages("abc", "Week 9.pptx", pages)
    assert len(chunks) > 1
    assert {c.page for c in chunks} == {4}  # the empty slide makes no text chunks
    assert all(c.doc_name == "Week 9.pptx" and c.label == "slide" for c in chunks)
    assert "Week 9.pptx, slide 4" in chunks[0].search_text()


def test_add_then_repeat_upload_is_not_duplicated(sample_pdf, tmp_path):
    library = new_library(tmp_path / "data")
    first = library.add_file(sample_pdf)
    copy = tmp_path / "same-file-new-name.pdf"
    shutil.copyfile(sample_pdf, copy)
    second = library.add_file(copy)
    assert first.added and not second.added
    assert "already in the library" in second.message
    assert len(library.list_documents()) == 1
    assert library.text_index.count() == len(first.document.chunks)
    assert library.image_index.count() == 3


def test_remove_deletes_everything_searchable(sample_pdf, sample_md, tmp_path):
    library = new_library(tmp_path / "data")
    pdf = library.add_file(sample_pdf).document
    library.add_file(sample_md)
    assert library.keyword_search("reranking", 5)
    library.remove(pdf.doc_id)
    assert [d.name for d in library.list_documents()] == ["syllabus.md"]
    assert library.keyword_search("reranking", 5) == []
    assert all(c.doc_id != pdf.doc_id for c, _ in library.text_vector_search("reranking chunks", 10))
    assert library.image_index.count() == 0
    assert not (tmp_path / "data" / "docs" / pdf.doc_id).exists()


def test_removal_keeps_remaining_text_index_queryable_after_image_deletion(sample_pdf, sample_md, tmp_path, monkeypatch):
    library = new_library(tmp_path / "data")
    pdf = library.add_file(sample_pdf).document
    library.add_file(sample_md)
    deletion_order = []
    original_image_delete = library.image_index.delete
    original_text_delete = library.text_index.delete

    def delete_images(*args, **kwargs):
        deletion_order.append("images")
        return original_image_delete(*args, **kwargs)

    def delete_text(*args, **kwargs):
        deletion_order.append("text")
        return original_text_delete(*args, **kwargs)

    monkeypatch.setattr(library.image_index, "delete", delete_images)
    monkeypatch.setattr(library.text_index, "delete", delete_text)
    library.remove(pdf.doc_id)
    assert deletion_order == ["images", "text"]
    assert library.text_vector_search("office hours", 10)


def test_library_survives_restart(sample_pdf, tmp_path):
    library = new_library(tmp_path / "data")
    doc = library.add_file(sample_pdf).document
    reopened = new_library(tmp_path / "data")
    assert [d.doc_id for d in reopened.list_documents()] == [doc.doc_id]
    assert reopened.keyword_search("chunking", 3)


def test_filter_by_document(sample_pdf, sample_md, tmp_path):
    library = new_library(tmp_path / "data")
    md = library.add_file(sample_md).document
    library.add_file(sample_pdf)
    hits = library.text_vector_search("office hours zoom", 10, doc_ids=[md.doc_id])
    assert hits and all(c.doc_id == md.doc_id for c, _ in hits)


def resaved_copy(source, path):
    """Same pages, different bytes: what re-saving or re-exporting a deck does."""
    document = pymupdf.open(source)
    document.set_metadata({"title": "Re-exported copy"})
    document.save(path, garbage=4, deflate=True)
    document.close()
    return path


def test_resaved_copy_is_not_duplicated(sample_pdf, tmp_path):
    library = new_library(tmp_path / "data")
    first = library.add_file(sample_pdf)
    copy = resaved_copy(sample_pdf, tmp_path / "MBAX 6418 - rag-notes v2.pdf")
    assert file_hash(copy) != file_hash(sample_pdf)
    second = library.add_file(copy)
    assert not second.added and second.document.doc_id == first.document.doc_id
    assert "already in the library" in second.message
    assert len(library.list_documents()) == 1
    assert [p.name for p in library.docs_dir.iterdir()] == [first.document.doc_id]  # no leftover folder
    assert library.text_index.count() == len(first.document.chunks)
    assert library.image_index.count() == 3


def test_same_text_with_a_changed_picture_is_added(sample_pdf, tmp_path):
    library = new_library(tmp_path / "data")
    library.add_file(sample_pdf)
    changed = tmp_path / "rag-notes-new-chart.pdf"
    document = pymupdf.open(sample_pdf)
    document[1].draw_rect(pymupdf.Rect(60, 300, 550, 700), color=(0, 0, 0), fill=(0.1, 0.2, 0.6))
    document.save(changed)
    document.close()
    assert library.add_file(changed).added
    assert len(library.list_documents()) == 2


def test_different_text_is_added(sample_pdf, tmp_path):
    library = new_library(tmp_path / "data")
    library.add_file(sample_pdf)
    other = make_pdf(tmp_path / "other.pdf", ["Embeddings", "Vector search", "Hybrid RAG"])
    assert library.add_file(other).added


def test_same_content_ignores_text_read_from_images():
    old = [Page(1, "slide", "[Text read from the image]\nONE DOES NOT SIMPLY", "", "", "80" * 256)]
    new = [Page(1, "slide", "", "", "", "82" * 256)]  # re-saving shifts brightness slightly
    assert same_content(new, old)


def test_picture_only_pages_without_fingerprints_are_not_matched():
    old = [Page(1, "slide", ""), Page(2, "slide", "")]
    new = [Page(1, "slide", ""), Page(2, "slide", "")]
    assert not same_content(new, old)


def test_removal_deletes_gradios_copies_of_page_images(sample_pdf, tmp_path, monkeypatch):
    from gradio import processing_utils

    from app import remove_document

    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio-cache"))
    library = new_library(tmp_path / "data")
    doc = library.add_file(sample_pdf).document
    # What Gradio does when the app shows these slides in an answer
    shown = [processing_utils.save_file_to_cache(p.image_path, str(tmp_path / "gradio-cache")) for p in doc.pages]
    assert all(Path(copy).exists() for copy in shown)
    assert "Removed" in remove_document(library, doc.doc_id)
    assert not any(Path(copy).exists() for copy in shown)
