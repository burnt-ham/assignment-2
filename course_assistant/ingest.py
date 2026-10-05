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
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

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

    def to_dict(self) -> dict:
        return asdict(self)


def file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def convert_to_pdf(path: Path, out_dir: Path, soffice: str = "soffice", timeout: int = 300) -> Path:
    """Convert an Office file to PDF with LibreOffice running headless."""
    if shutil.which(soffice) is None:
        raise IngestError(
            "LibreOffice is needed to read PowerPoint and Word files, but it isn't installed. "
            "Install LibreOffice, or export the file to PDF and upload the PDF."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        command = [
            soffice,
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(path),
        ]
        try:
            subprocess.run(command, capture_output=True, timeout=timeout, check=False)
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
            pages.append(Page(number=index, label=label, text=text, title=_first_line(text), image_path=str(image_path)))
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


def read_document(path: str | Path, work_dir: Path, soffice: str = "soffice") -> list[Page]:
    """Read any supported file into pages. Page images are written under `work_dir`."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        raise IngestError(f"{path.name} isn't a supported file type. Supported: {SUPPORTED_DESCRIPTION}")
    if suffix in TEXT_TYPES:
        return read_text(path)
    image_dir = work_dir / "pages"
    if suffix in PDF_TYPES:
        return read_pdf(path, image_dir, label="page")
    pdf = convert_to_pdf(path, work_dir / "converted", soffice=soffice)
    label = "slide" if suffix in {".pptx", ".ppt"} else "page"
    return read_pdf(pdf, image_dir, label=label)
