"""Turn an uploaded file into pages: text plus an image of each page or slide.

Supported formats:
- PDF: each page becomes one page, with its text and a rendered image.
- PowerPoint (.pptx, .ppt) and Word (.docx, .doc): converted to PDF with
  LibreOffice first, so every slide keeps its original look.
- Text (.txt) and Markdown (.md): split into sections at headings. These have
  no images.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import warnings
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree

import pymupdf
from PIL import Image, ImageFilter

PDF_TYPES = {".pdf"}
OFFICE_TYPES = {".pptx", ".ppt", ".docx", ".doc"}
TEXT_TYPES = {".txt", ".md"}
SUPPORTED_TYPES = PDF_TYPES | OFFICE_TYPES | TEXT_TYPES

SUPPORTED_DESCRIPTION = (
    "PDF (.pdf), PowerPoint (.pptx, .ppt), Word (.docx, .doc), text (.txt) and Markdown (.md). "
    "PowerPoint and Word files are converted to PDF with LibreOffice so slide images look like the originals."
)

RENDER_DPI = 110

pymupdf.TOOLS.mupdf_display_errors(False)  # embedded videos in slides cause harmless warnings


class IngestError(Exception):
    """A file couldn't be read. The message is safe to show to users."""


@dataclass
class Page:
    number: int  # 1-based page, slide or section number
    label: str  # "slide", "page" or "section"
    text: str
    title: str = ""
    image_path: str = ""  # empty for text and Markdown files
    signature: str = ""  # image_signature() of the page image, to spot re-saved copies

    def to_dict(self) -> dict:
        return asdict(self)


def file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def image_signature(path: str | Path) -> str:
    """A small fingerprint of how a page looks: its brightness on a 16x16 grid, as hex.

    Re-saving or re-exporting a deck barely changes it (under 1/20 of the range in
    any cell for the course decks), while a swapped picture or chart moves whole
    cells, so it tells a re-saved copy from an edited one.
    """
    with Image.open(path) as image:
        return image.convert("L").resize((16, 16), Image.BOX).tobytes().hex()


GIF_MAX_SIDE = 1600


def _detail(frame: Image.Image) -> float:
    """How much is drawn in a frame: the average edge strength of a small greyscale copy."""
    small = frame.convert("L")
    small.thumbnail((256, 256))
    edges = small.filter(ImageFilter.FIND_EDGES)
    return sum(edges.getdata()) / (edges.width * edges.height)


def most_detailed_frame(gif: Image.Image) -> Image.Image:
    """Pick the frame with the most drawn on it (the earliest one wins ties).

    Animations often start empty and build up a diagram (Week 5 slide 8 starts
    as a blank beige box), so the first frame can carry nothing to search or describe.
    """
    best, best_detail = None, -1.0
    for index in range(gif.n_frames):
        gif.seek(index)
        frame = gif.convert("RGB")
        detail = _detail(frame)
        if detail > best_detail:
            best, best_detail = frame, detail
    return best


# An opening <a:rPr ...> (not a self-closing <a:rPr .../>) whose settings, before its
# closing tag, include a click link. The settings can't reach past this run's own rPr.
_LINKED_RUN = re.compile(
    rb"<a:rPr\b([^>]*?)(?<!/)>((?:(?!</a:rPr>|<a:rPr\b).)*?)"
    rb"<a:hlinkClick\b[^>]*?(?:/>|>(?:(?!</a:rPr>).)*?</a:hlinkClick>)"
    rb"((?:(?!</a:rPr>|<a:rPr\b).)*?)</a:rPr>",
    re.S,
)
_LINK_COLOUR = b'<a:solidFill><a:schemeClr val="hlink"/></a:solidFill>'


def _plain_link(match: re.Match) -> bytes:
    attrs, before, after = match.groups()
    if b" u=" not in attrs:
        attrs += b' u="sng"'  # keep the underline links are drawn with
    children = before + after
    if b"<a:solidFill>" not in children and b"<a:gradFill>" not in children:
        # A fill goes first among the run's settings (after an outline, if there is one)
        outline = re.match(rb"<a:ln\b.*?(?:/>|</a:ln>)", children, re.S)
        cut = outline.end() if outline else 0
        children = children[:cut] + _LINK_COLOUR + children[cut:]
    return b"<a:rPr" + attrs + b">" + children + b"</a:rPr>"


def unlink_text(slide_xml: bytes) -> tuple[bytes, int]:
    """Turn linked text into plain text drawn like a link (theme link colour, underlined).

    LibreOffice treats linked text as one unbreakable piece, so a link too long
    for its line wraps mid-word ("Artificial Analysis I / ntelligence Index" on
    Week 2 slide 7), and the split word also reaches the searchable text.
    Plain text wraps at spaces, as in PowerPoint. Slide images can't be clicked anyway.
    """
    changed, count = _LINKED_RUN.subn(_plain_link, slide_xml)
    if count:
        try:
            ElementTree.fromstring(changed)
        except ElementTree.ParseError:
            return slide_xml, 0  # never hand LibreOffice a broken slide; keep the original
    return changed, count


def prepare_pptx(path: Path, out_dir: Path) -> tuple[Path, int]:
    """Copy a .pptx with the fixes LibreOffice needs to draw it like PowerPoint.

    - Each animated GIF becomes a still of its most detailed frame. LibreOffice
      draws some large animated GIFs as an empty box (Week 2 slide 37), and the
      most detailed frame gives the image search and the vision model something
      to work with when an animation starts blank. Keeping the same file name
      keeps the slide's links to it working.
    - Linked text becomes plain text styled as a link (see unlink_text).

    Returns the prepared copy and how many GIFs were frozen.
    """
    try:
        source = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return path, 0  # not a zip; let LibreOffice report the problem
    out_dir.mkdir(parents=True, exist_ok=True)
    frozen_path = out_dir / path.name
    frozen = 0
    with source, zipfile.ZipFile(frozen_path, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item)
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", item.filename):
                data, _ = unlink_text(data)
            elif item.filename.startswith("ppt/media/") and item.filename.lower().endswith(".gif"):
                try:
                    with Image.open(io.BytesIO(data)) as gif, warnings.catch_warnings():
                        warnings.simplefilter("ignore")  # palette-transparency notices are harmless
                        if getattr(gif, "n_frames", 1) > 1:
                            frame = most_detailed_frame(gif)
                            frame.thumbnail((GIF_MAX_SIDE, GIF_MAX_SIDE))
                            buffer = io.BytesIO()
                            frame.save(buffer, format="GIF")
                            data = buffer.getvalue()
                            frozen += 1
                except Exception:  # Pillow raises several error types for unusual files
                    pass  # unreadable image: leave it for LibreOffice
            target.writestr(item, data)
    return frozen_path, frozen


def convert_to_pdf(path: Path, out_dir: Path, soffice: str = "soffice", timeout: int = 300) -> Path:
    """Convert an Office file to PDF with LibreOffice running headless."""
    if shutil.which(soffice) is None:
        raise IngestError(
            "LibreOffice is needed to read PowerPoint and Word files, but it isn't installed. "
            "Install LibreOffice, or export the file to PDF and upload the PDF."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        windows = os.name == "nt"
        command = [soffice]
        if not windows:
            # On Windows this flag stops the conversion working, so it's left out there
            command.append(f"-env:UserInstallation=file://{profile}")
        command += [
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(path),
        ]
        try:
            subprocess.run(command, capture_output=True, timeout=timeout, check=False, shell=windows)
        except subprocess.TimeoutExpired as exc:
            raise IngestError(f"Converting {path.name} took too long and was stopped.") from exc
    pdf = out_dir / (path.stem + ".pdf")
    if not pdf.exists():
        raise IngestError(
            f"LibreOffice couldn't convert {path.name}. Try exporting it to PDF yourself and uploading the PDF."
        )
    return pdf


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip(" •\t")
        if line:
            return line[:120]
    return ""


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t\u00a0]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def drop_repeated_lines(texts: list[str]) -> list[str]:
    """Remove header and footer lines that repeat on most pages, e.g. "Course Syllabus 3" on page 3."""
    if len(texts) < 4:
        return texts

    def key(line: str, page_number: int) -> str:
        line = line.strip().lower()
        suffix = str(page_number)
        if line.endswith(suffix) and not line[: -len(suffix)].rstrip().endswith(tuple("0123456789")):
            line = line[: -len(suffix)].rstrip()  # drop a trailing page number
        return line

    counts: dict[str, int] = {}
    for number, text in enumerate(texts, start=1):
        for line_key in {key(line, number) for line in text.splitlines() if line.strip()}:
            counts[line_key] = counts.get(line_key, 0) + 1
    repeated = {k for k, n in counts.items() if n >= max(3, len(texts) // 2) and len(k) > 3}
    return [
        "\n".join(line for line in text.splitlines() if key(line, number) not in repeated).strip()
        for number, text in enumerate(texts, start=1)
    ]


def read_pdf(pdf_path: Path, image_dir: Path, label: str = "page") -> list[Page]:
    image_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Page] = []
    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:  # pymupdf raises several error types
        raise IngestError(f"{pdf_path.name} couldn't be opened as a PDF.") from exc
    with document:
        for index, page in enumerate(document, start=1):
            text = clean_text(page.get_text())
            image_path = image_dir / f"{label}-{index:03d}.png"
            page.get_pixmap(dpi=RENDER_DPI).save(image_path)
            pages.append(
                Page(
                    number=index,
                    label=label,
                    text=text,
                    title=_first_line(text),
                    image_path=str(image_path),
                    signature=image_signature(image_path),
                )
            )
    if not pages:
        raise IngestError(f"{pdf_path.name} has no pages.")
    for page, text in zip(pages, drop_repeated_lines([p.text for p in pages])):
        page.text = text
        page.title = _first_line(text)
    return pages


_HEADING = re.compile(r"^#{1,6}\s+(.+)$")


def read_text(path: Path) -> list[Page]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in raw.splitlines():
        heading = _HEADING.match(line.strip()) if path.suffix.lower() == ".md" else None
        if heading:
            sections.append((heading.group(1).strip(), [line]))
        else:
            sections[-1][1].append(line)
    pages = []
    for title, lines in sections:
        text = "\n".join(lines).strip()
        if text:
            pages.append(Page(number=len(pages) + 1, label="section", text=text, title=title or _first_line(text)))
    if not pages:
        raise IngestError(f"{path.name} is empty.")
    return pages


def _find_soffice() -> str:
    """Find soffice executable, trying the usual Windows and Mac install folders if not in PATH."""
    import shutil
    # On Windows, soffice.com is the headless entry point; .exe can hang
    for candidate in [
        "C:/Program Files/LibreOffice/program/soffice.com",
        "C:/Program Files (x86)/LibreOffice/program/soffice.com",
        # Mac installs from libreoffice.org don't add soffice to PATH (Homebrew does)
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]:
        if os.path.isfile(candidate):
            return candidate
    # Fall back to PATH lookup
    for name in ("soffice.com", "soffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    return "soffice"


def read_document(path: str | Path, work_dir: Path, soffice: str | None = None) -> list[Page]:
    """Read any supported file into pages. Page images are written under `work_dir`."""
    if soffice in (None, "soffice"):
        # No custom SOFFICE_PATH: look in the usual install folders as well as PATH
        soffice = _find_soffice()
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        raise IngestError(f"{path.name} isn't a supported file type. Supported: {SUPPORTED_DESCRIPTION}")
    if suffix in TEXT_TYPES:
        return read_text(path)
    image_dir = work_dir / "pages"
    if suffix in PDF_TYPES:
        return read_pdf(path, image_dir, label="page")
    prepared = work_dir / "prepared"
    if suffix == ".pptx":
        path, _ = prepare_pptx(path, prepared)
    try:
        pdf = convert_to_pdf(path, work_dir / "converted", soffice=soffice)
    finally:
        shutil.rmtree(prepared, ignore_errors=True)  # the prepared copy is only needed for conversion
    label = "slide" if suffix in {".pptx", ".ppt"} else "page"
    return read_pdf(pdf, image_dir, label=label)
