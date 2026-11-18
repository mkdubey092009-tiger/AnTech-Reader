import os
import threading
import pymupdf  # PyMuPDF
from pdf2docx import Converter
import pyttsx3


class PDFEngine:
    """Backend engine providing Acrobat Reader Free + Pro features."""

    def __init__(self):
        self.doc: pymupdf.Document | None = None
        self.filepath: str | None = None
        self.zoom: float = 1.3
        self.dark_mode: bool = False

    # -------------------- FILE & SECURITY --------------------

    def open_pdf(self, filepath: str, password: str | None = None) -> tuple[bool, str]:
        """Opens a PDF file, handling password-protected documents."""
        try:
            doc = pymupdf.open(filepath)
            if doc.needs_pass:
                if not password or not doc.authenticate(password):
                    return False, "PASSWORD_REQUIRED"
            if self.doc:
                self.doc.close()
            self.doc = doc
            self.filepath = filepath
            return True, "Opened successfully"
        except Exception as e:
            return False, str(e)

    def save_pdf(self, output_path: str | None = None):
        """Saves changes incrementally or to a new file."""
        if not self.doc:
            return
        target = output_path or self.filepath
        if target == self.filepath and self.doc.can_save_incrementally():
            self.doc.saveIncr()
        else:
            self.doc.save(target, garbage=3, deflate=True)
            self.filepath = target

    def encrypt_pdf(self, output_path: str, user_pw: str, owner_pw: str):
        """Protects the PDF using AES-256 encryption."""
        if not self.doc:
            return
        perms = int(
            pymupdf.PDF_PERM_ACCESSIBILITY
            | pymupdf.PDF_PERM_PRINT
            | pymupdf.PDF_PERM_COPY
            | pymupdf.PDF_PERM_ANNOTATE
        )
        self.doc.save(
            output_path,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw=owner_pw or user_pw,
            user_pw=user_pw,
            permissions=perms,
        )

    def compress_pdf(self, output_path: str):
        """Reduces PDF file size via image downsampling and stream compression."""
        if not self.doc:
            return
        try:
            self.doc.rewrite_images(dpi_threshold=150, dpi_target=96, quality=75)
        except Exception:
            pass
        self.doc.save(output_path, garbage=4, deflate=True, clean=True)

    # -------------------- RENDERING & SEARCH --------------------

    def render_page(self, page_num: int, zoom: float | None = None) -> bytes | None:
        """Renders a PDF page to PNG bytes, supporting zoom and dark mode."""
        if not self.doc or not (0 <= page_num < len(self.doc)):
            return None
        z = zoom if zoom is not None else self.zoom
        page = self.doc[page_num]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        if self.dark_mode:
            pix.invert_irect(pix.irect)
        return pix.tobytes("png")

    def render_thumbnail(self, page_num: int) -> bytes | None:
        """Renders a lightweight thumbnail for the sidebar."""
        return self.render_page(page_num, zoom=0.22)

    def search_text(self, query: str) -> dict[int, list[pymupdf.Rect]]:
        """Searches text across all pages and returns bounding boxes per page."""
        results = {}
        if not self.doc or not query.strip():
            return results
        for idx, page in enumerate(self.doc):
            rects = page.search_for(query)
            if rects:
                results[idx] = rects
        return results

    # -------------------- ANNOTATIONS & FILL/SIGN --------------------

    def add_highlight(self, page_num: int, rect: pymupdf.Rect):
        if not self.doc:
            return
        annot = self.doc[page_num].add_highlight_annot(rect)
        annot.set_colors(stroke=(1, 0.92, 0.23))
        annot.update()

    def add_sticky_note(self, page_num: int, point: pymupdf.Point, text: str):
        if not self.doc or not text.strip():
            return
        annot = self.doc[page_num].add_text_annot(point, text, icon="Note")
        annot.update()

    def add_freetext(self, page_num: int, rect: pymupdf.Rect, text: str):
        if not self.doc or not text.strip():
            return
        annot = self.doc[page_num].add_freetext_annot(
            rect,
            text,
            fontsize=12,
            text_color=(0.05, 0.15, 0.65),
            fill_color=(1, 1, 0.9),
        )
        annot.update()

    def add_ink_drawing(self, page_num: int, points: list[tuple[float, float]]):
        if not self.doc or len(points) < 2:
            return
        annot = self.doc[page_num].add_ink_annot([points])
        annot.set_colors(stroke=(0.85, 0.1, 0.1))
        annot.set_border(width=2.0)
        annot.update()

    def insert_signature(self, page_num: int, rect: pymupdf.Rect, sig_image_path: str):
        if not self.doc or not os.path.exists(sig_image_path):
            return
        self.doc[page_num].insert_image(rect, filename=sig_image_path)

    def clear_page_annotations(self, page_num: int):
        if not self.doc:
            return
        page = self.doc[page_num]
        for annot in list(page.annots()):
            page.delete_annot(annot)

    # -------------------- ORGANIZE PAGES (MERGE/SPLIT/EDIT) --------------------

    def rotate_page(self, page_num: int, angle: int = 90):
        if not self.doc:
            return
        page = self.doc[page_num]
        page.set_rotation((page.rotation + angle) % 360)

    def delete_page(self, page_num: int):
        if self.doc and len(self.doc) > 1:
            self.doc.delete_page(page_num)

    def move_page(self, from_idx: int, to_idx: int):
        if self.doc and 0 <= from_idx < len(self.doc) and 0 <= to_idx < len(self.doc):
            self.doc.move_page(from_idx, to_idx)

    @staticmethod
    def merge_pdfs(pdf_paths: list[str], output_path: str):
        merged = pymupdf.open()
        for path in pdf_paths:
            with pymupdf.open(path) as part:
                merged.insert_pdf(part)
        merged.save(output_path, garbage=3, deflate=True)
        merged.close()

    def split_pdf(self, start_page: int, end_page: int, output_path: str):
        """Extracts a 1-indexed inclusive page range into a new PDF."""
        if not self.doc:
            return
        new_doc = pymupdf.open()
        new_doc.insert_pdf(self.doc, from_page=start_page - 1, to_page=end_page - 1)
        new_doc.save(output_path, garbage=3, deflate=True)
        new_doc.close()

    # -------------------- CONVERT, OCR & READ ALOUD --------------------

    def pdf_to_word(self, output_docx: str):
        if not self.filepath:
            return
        cv = Converter(self.filepath)
        cv.convert(output_docx)
        cv.close()

    def pdf_to_images(self, output_dir: str, dpi: int = 200):
        if not self.doc:
            return
        os.makedirs(output_dir, exist_ok=True)
        for i, page in enumerate(self.doc):
            pix = page.get_pixmap(dpi=dpi)
            pix.save(os.path.join(output_dir, f"page_{i + 1:03d}.png"))

    @staticmethod
    def images_to_pdf(image_paths: list[str], output_pdf: str):
        out = pymupdf.open()
        for img_path in image_paths:
            with pymupdf.open(img_path) as img:
                pdf_bytes = img.convert_to_pdf()
            with pymupdf.open("pdf", pdf_bytes) as img_pdf:
                out.insert_pdf(img_pdf)
        out.save(output_pdf, garbage=3, deflate=True)
        out.close()

    def extract_text_or_ocr(self, page_num: int) -> str:
        """Extracts embedded text, falling back to Tesseract OCR for scanned pages."""
        if not self.doc:
            return ""
        page = self.doc[page_num]
        text = page.get_text("text").strip()
        if text:
            return text
        try:
            ocr_tp = page.get_textpage_ocr(language="eng", full=True)
            return ocr_tp.extractText().strip()
        except Exception as e:
            return f"[Scanned page detected. Install Tesseract OCR to extract text: {e}]"

    def read_aloud(self, page_num: int):
        """Speaks the current page text asynchronously using offline TTS."""
        text = self.extract_text_or_ocr(page_num)
        if not text:
            return

        def _speak():
            engine = pyttsx3.init()
            engine.setProperty("rate", 165)
            engine.say(text)
            engine.runAndWait()

        threading.Thread(target=_speak, daemon=True).start()
