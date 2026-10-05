"""Unit tests: adding, de-duplicating and removing documents in the library."""

import shutil

from course_assistant.library import Library, chunk_pages
from course_assistant.ingest import Page
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
