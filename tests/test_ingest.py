"""Unit tests: reading files into pages."""

import shutil

import pytest
from pptx import Presentation

from course_assistant.ingest import IngestError, drop_repeated_lines, file_hash, read_document, read_text


def test_pdf_pages_keep_text_and_images(sample_pdf, tmp_path):
    pages = read_document(sample_pdf, tmp_path / "work")
    assert [p.number for p in pages] == [1, 2, 3]
    assert pages[0].label == "page"
    assert "retrieves relevant information" in pages[0].text
    for page in pages:
        assert page.image_path and (tmp_path / "work" / "pages").exists()


def test_markdown_splits_into_sections(sample_md):
    pages = read_text(sample_md)
    assert [p.title for p in pages] == ["Office hours", "Grading"]
    assert all(p.label == "section" and not p.image_path for p in pages)


def test_unsupported_type_is_rejected(tmp_path):
    path = tmp_path / "data.xlsx"
    path.write_bytes(b"not a real spreadsheet")
    with pytest.raises(IngestError, match="isn't a supported file type"):
        read_document(path, tmp_path / "work")


def test_empty_text_file_is_rejected(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(IngestError, match="empty"):
        read_document(path, tmp_path / "work")


def test_repeated_headers_and_footers_are_removed():
    texts = [f"Topic {chr(64 + i)}\nreal content {i * 10}\nCourse Syllabus (Subject to Change) {i}" for i in range(1, 7)]
    cleaned = drop_repeated_lines(texts)
    assert all("Subject to Change" not in t for t in cleaned)
    assert cleaned[2].startswith("Topic C")
    assert "real content 30" in cleaned[2]


def test_same_bytes_give_same_hash(sample_pdf, tmp_path):
    copy = tmp_path / "renamed.pdf"
    shutil.copyfile(sample_pdf, copy)
    assert file_hash(sample_pdf) == file_hash(copy)


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_powerpoint_is_converted_to_slide_images(tmp_path):
    deck = Presentation()
    for title in ["Prompt engineering", "Structured output"]:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = f"Notes about {title.lower()}"
    path = tmp_path / "week.pptx"
    deck.save(path)
    pages = read_document(path, tmp_path / "work")
    assert [p.label for p in pages] == ["slide", "slide"]
    assert pages[1].title == "Structured output"
    assert all(p.image_path for p in pages)
