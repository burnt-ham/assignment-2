"""Unit tests: reading files into pages."""

import io
import shutil
import zipfile

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from course_assistant.ingest import (
    IngestError,
    drop_repeated_lines,
    file_hash,
    freeze_animated_gifs,
    read_document,
    read_text,
)


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


def _deck_with_animated_gif(tmp_path):
    # Starts blank, then draws a navy box: the box is the frame worth keeping
    frames = [Image.new("RGB", (64, 48), "white") for _ in range(3)]
    for frame in frames[1:]:
        frame.paste("navy", (8, 8, 56, 40))
    gif = io.BytesIO()
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    gif.seek(0)
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "Terminal demo"
    slide.shapes.add_picture(gif, Inches(1), Inches(2), width=Inches(6))
    path = tmp_path / "gif-deck.pptx"
    deck.save(path)
    return path


def test_animated_gifs_become_their_most_detailed_frame(tmp_path):
    deck = _deck_with_animated_gif(tmp_path)
    frozen_path, frozen = freeze_animated_gifs(deck, tmp_path / "prepared")
    assert frozen == 1
    with zipfile.ZipFile(deck) as before, zipfile.ZipFile(frozen_path) as after:
        assert before.namelist() == after.namelist()  # same parts, so slide links still work
        gif_name = next(n for n in after.namelist() if n.endswith(".gif"))
        with Image.open(io.BytesIO(after.read(gif_name))) as still:
            assert getattr(still, "n_frames", 1) == 1
            red, green, blue = still.convert("RGB").getpixel((32, 24))
            assert blue > 100 and red < 50  # navy: the drawn frame, not the blank first one


def test_non_zip_file_is_left_alone(tmp_path):
    path = tmp_path / "old.pptx"
    path.write_bytes(b"not a zip")
    assert freeze_animated_gifs(path, tmp_path / "prepared") == (path, 0)


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_slide_with_animated_gif_shows_the_drawn_frame(tmp_path):
    # End-to-end check: the converted slide shows the navy box, not the blank first frame.
    pages = read_document(_deck_with_animated_gif(tmp_path), tmp_path / "work")
    with Image.open(pages[0].image_path) as image:
        navy = sum(1 for r, g, b in image.convert("RGB").getdata() if b > 100 and r < 50 and g < 50)
    assert navy > 1000  # the drawn frame, not an empty box
