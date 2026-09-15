"""
Raw text extraction from resume files.

PDFs: extracted page-by-page with PyMuPDF. Any page whose extracted text is
suspiciously short (i.e. it's a scanned image, not real text) is rendered to
an image and passed through Tesseract OCR with Azerbaijani+Russian+English
language packs instead.

DOCX: paragraphs + table cell text via python-docx (DOCX text layer is
always real text, so no OCR path is needed there).
"""
import io

import docx
import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from app.core.config import settings

# Pages with fewer extracted characters than this are treated as image-only
# and re-processed with OCR. Real text-layer pages (even short resumes) are
# comfortably above this threshold; scanned pages with no text layer report ~0.
MIN_TEXT_CHARS_PER_PAGE = 20

# Render resolution for OCR - higher improves accuracy on small print /
# non-Latin scripts at the cost of speed.
OCR_RENDER_DPI = 300


class TextExtractionError(Exception):
    pass


def _ocr_page(page: fitz.Page) -> str:
    pix = page.get_pixmap(dpi=OCR_RENDER_DPI)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(image, lang=settings.OCR_LANGUAGES).strip()


def extract_text_from_pdf(file_path: str) -> str:
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        raise TextExtractionError(f"Could not open PDF: {e}") from e

    pages_text: list[str] = []
    try:
        for page in doc:
            text = page.get_text().strip()
            if len(text) < MIN_TEXT_CHARS_PER_PAGE:
                text = _ocr_page(page)
            pages_text.append(text)
    finally:
        doc.close()

    return "\n\n".join(p for p in pages_text if p)


def extract_text_from_docx(file_path: str) -> str:
    try:
        document = docx.Document(file_path)
    except Exception as e:
        raise TextExtractionError(f"Could not open DOCX: {e}") from e

    parts: list[str] = [p.text.strip() for p in document.paragraphs if p.text.strip()]

    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text.strip())

    return "\n".join(parts)


def extract_text(file_path: str, filename: str) -> str:
    if "." not in filename:
        raise TextExtractionError(f"File has no extension: {filename}")

    ext = filename.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        text = extract_text_from_pdf(file_path)
    elif ext == "docx":
        text = extract_text_from_docx(file_path)
    else:
        raise TextExtractionError(f"Unsupported file type: .{ext} (only .pdf and .docx are supported)")

    if not text.strip():
        raise TextExtractionError(
            "No text could be extracted from this file, even after OCR. "
            "The file may be corrupted, empty, or an unsupported scan quality."
        )
    return text
