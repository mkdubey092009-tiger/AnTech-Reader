import sys
import pymupdf
from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QAction
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QToolBar,
    QLabel,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QDialog,
)
from PySide6.QtGui import QIcon

from pdf_engine import PDFEngine
from ui_dialogs import SignatureDialog, SplitDialog, EncryptDialog


class InteractivePDFCanvas(QLabel):
    """Interactive PDF viewport supporting drag-highlight, ink drawing, notes, text, and signatures."""

    annotation_added = Signal()

    def __init__(self, engine: PDFEngine):
        super().__init__()
        self.engine = engine
        self.page_num = 0
        self.tool_mode = "pan"  # pan | highlight | note | text | ink | sign
        self.pending_sig_path: str | None = None
        self.search_rects: list[pymupdf.Rect] = []

        self.drag_start: QPoint | None = None
        self.drag_curr: QPoint | None = None
        self.ink_points: list[tuple[float, float]] = []

        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setMouseTracking(True)

    def _to_pdf_point(self, pt: QPoint) -> pymupdf.Point:
        z = self.engine.zoom
        return pymupdf.Point(pt.x() / z, pt.y() / z)

    def _to_pdf_rect(self, p1: QPoint, p2: QPoint) -> pymupdf.Rect:
        z = self.engine.zoom
        x0, x1 = sorted([p1.x() / z, p2.x() / z])
        y0, y1 = sorted([p1.y() / z, p2.y() / z])
        return pymupdf.Rect(x0, y0, x1, y1)

    def mousePressEvent(self, event):
        if not self.engine.doc or event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()

        if self.tool_mode == "note":
            text, ok = QInputDialog.getMultiLineText(self, "Sticky Note", "Comment:")
            if ok and text.strip():
                self.engine.add_sticky_note(self.page_num, self._to_pdf_point(pos), text)
                self.annotation_added.emit()

        elif self.tool_mode == "text":
            text, ok = QInputDialog.getText(self, "Add Text", "Enter text to insert:")
            if ok and text.strip():
                pt = self._to_pdf_point(pos)
                rect = pymupdf.Rect(pt.x, pt.y, pt.x + 220, pt.y + 40)
                self.engine.add_freetext(self.page_num, rect, text)
                self.annotation_added.emit()

        elif self.tool_mode == "sign" and self.pending_sig_path:
            pt = self._to_pdf_point(pos)
            rect = pymupdf.Rect(pt.x, pt.y, pt.x + 150, pt.y + 60)
            self.engine.insert_signature(self.page_num, rect, self.pending_sig_path)
            self.tool_mode = "pan"
            self.annotation_added.emit()

        elif self.tool_mode in ("highlight", "ink"):
            self.drag_start = pos
            self.drag_curr = pos
            if self.tool_mode == "ink":
                pt = self._to_pdf_point(pos)
                self.ink_points = [(pt.x, pt.y)]

    def mouseMoveEvent(self, event):
        if self.drag_start is None:
            return
        pos = event.position().toPoint()
        self.drag_curr = pos
        if self.tool_mode == "ink":
            pt = self._to_pdf_point(pos)
            self.ink_points.append((pt.x, pt.y))
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drag_start is None or event.button() != Qt.LeftButton:
            return
        end_pos = event.position().toPoint()

        if self.tool_mode == "highlight":
            rect = self._to_pdf_rect(self.drag_start, end_pos)
            if rect.width > 3 and rect.height > 3:
                self.engine.add_highlight(self.page_num, rect)
                self.annotation_added.emit()

        elif self.tool_mode == "ink":
            if len(self.ink_points) > 1:
                self.engine.add_ink_drawing(self.page_num, self.ink_points)
                self.annotation_added.emit()

        self.drag_start = None
        self.drag_curr = None
        self.ink_points = []
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        z = self.engine.zoom

        # Highlight search matches
        if self.search_rects:
            painter.setBrush(QColor(255, 235, 59, 110))
            painter.setPen(QPen(QColor(245, 158, 11), 2))
            for r in self.search_rects:
                painter.drawRect(QRect(int(r.x0 * z), int(r.y0 * z), int(r.width * z), int(r.height * z)))

        # Live selection preview while dragging
        if self.drag_start and self.drag_curr:
            if self.tool_mode == "highlight":
                painter.setBrush(QColor(255, 235, 59, 90))
                painter.setPen(QPen(QColor(234, 179, 8), 1, Qt.DashLine))
                painter.drawRect(QRect(self.drag_start, self.drag_curr).normalized())
            elif self.tool_mode == "ink" and len(self.ink_points) > 1:
                painter.setPen(QPen(QColor(220, 38, 38), 2.5))
                for i in range(1, len(self.ink_points)):
                    p1 = QPoint(int(self.ink_points[i - 1][0] * z), int(self.ink_points[i - 1][1] * z))
                    p2 = QPoint(int(self.ink_points[i][0] * z), int(self.ink_points[i][1] * z))
                    painter.drawLine(p1, p2)


class OpenAcrobatWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenAcrobat - Free & Open-Source PDF Reader & Editor")
        self.resize(1360, 860)

        self.engine = PDFEngine()
        self.current_page = 0
        self.search_hits: dict[int, list[pymupdf.Rect]] = {}

        self._build_ui()
        self._build_menus_and_toolbar()

    def _build_ui(self):
        splitter = QSplitter(Qt.Horizontal)

        # Left Thumbnail Sidebar
        self.thumb_list = QListWidget()
        self.thumb_list.setMaximumWidth(190)
        self.thumb_list.setIconSize(QPixmap(130, 170).size())
        self.thumb_list.currentRowChanged.connect(self._on_thumbnail_selected)
        splitter.addWidget(self.thumb_list)

        # Center Scrollable PDF Viewport
        self.canvas = InteractivePDFCanvas(self.engine)
        self.canvas.annotation_added.connect(self.refresh_view)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.canvas)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setStyleSheet("background-color: #1e293b;")
        splitter.addWidget(self.scroll_area)

        splitter.setSizes([180, 1180])
        self.setCentralWidget(splitter)

    def _build_menus_and_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)

        # File Operations
        self._add_action(tb, "Open", self.open_file)
        self._add_action(tb, "Save", self.save_file)
        self._add_action(tb, "Save As", self.save_file_as)
        tb.addSeparator()

        # Navigation & Zoom
        self._add_action(tb, "Prev", lambda: self.go_to_page(self.current_page - 1))
        self.page_label = QLabel(" Page 0 / 0 ")
        tb.addWidget(self.page_label)
        self._add_action(tb, "Next", lambda: self.go_to_page(self.current_page + 1))
        self._add_action(tb, "Zoom -", lambda: self.change_zoom(-0.2))
        self._add_action(tb, "Zoom +", lambda: self.change_zoom(0.2))
        self._add_action(tb, "Dark Mode", self.toggle_dark_mode)
        tb.addSeparator()

        # Annotate & Fill/Sign
        self._add_action(tb, "Pan", lambda: self.set_mode("pan"))
        self._add_action(tb, "Highlight", lambda: self.set_mode("highlight"))
        self._add_action(tb, "Sticky Note", lambda: self.set_mode("note"))
        self._add_action(tb, "Add Text", lambda: self.set_mode("text"))
        self._add_action(tb, "Draw", lambda: self.set_mode("ink"))
        self._add_action(tb, "Fill & Sign", self.start_sign_mode)
        self._add_action(tb, "Clear Annots", self.clear_annots)
        tb.addSeparator()

        # Search Bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search PDF...")
        self.search_input.setMaximumWidth(170)
        self.search_input.returnPressed.connect(self.run_search)
        tb.addWidget(self.search_input)
        btn_search = QPushButton("Find")
        btn_search.clicked.connect(self.run_search)
        tb.addWidget(btn_search)

        # Top Menu Bar (Pro Tools: Organize, Convert, Compress, Protect, OCR, Read Aloud)
        menubar = self.menuBar()

        org_menu = menubar.addMenu("Organize Pages")
        org_menu.addAction("Rotate Page 90° CW", self.rotate_current_page)
        org_menu.addAction("Move Page Up", lambda: self.move_current_page(-1))
        org_menu.addAction("Move Page Down", lambda: self.move_current_page(1))
        org_menu.addAction("Delete Current Page", self.delete_current_page)
        org_menu.addSeparator()
        org_menu.addAction("Merge Multiple PDFs...", self.merge_pdfs)
        org_menu.addAction("Split / Extract Page Range...", self.split_pdf)

        conv_menu = menubar.addMenu("Convert & Create")
        conv_menu.addAction("Export PDF to Word (.docx)...", self.export_word)
        conv_menu.addAction("Export PDF Pages to PNG Images...", self.export_images)
        conv_menu.addAction("Create PDF from Images...", self.create_pdf_from_images)

        sec_menu = menubar.addMenu("Optimize & Security")
        sec_menu.addAction("Compress PDF Size...", self.compress_pdf)
        sec_menu.addAction("Password Protect PDF (AES-256)...", self.encrypt_pdf)

        ai_menu = menubar.addMenu("Accessibility & OCR")
        ai_menu.addAction("Read Current Page Aloud (TTS)", self.read_aloud)
        ai_menu.addAction("Extract Text / Run OCR on Page", self.show_ocr_text)

    def _add_action(self, toolbar: QToolBar, label: str, slot):
        act = QAction(label, self)
        act.triggered.connect(slot)
        toolbar.addAction(act)

    # -------------------- VIEW & NAVIGATION --------------------

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        ok, msg = self.engine.open_pdf(path)
        if not ok and msg == "PASSWORD_REQUIRED":
            pw, accepted = QInputDialog.getText(
                self, "Encrypted PDF", "Enter PDF password:", QLineEdit.Password
            )
            if accepted:
                ok, msg = self.engine.open_pdf(path, pw)
        if not ok:
            QMessageBox.critical(self, "Error", f"Could not open PDF:\n{msg}")
            return

        self.current_page = 0
        self.search_hits.clear()
        self.populate_thumbnails()
        self.refresh_view()

    def populate_thumbnails(self):
        self.thumb_list.blockSignals(True)
        self.thumb_list.clear()
        if self.engine.doc:
            for i in range(len(self.engine.doc)):
                png = self.engine.render_thumbnail(i)
                pix = QPixmap()
                pix.loadFromData(png)
                item = QListWidgetItem(QIcon(pix), f"Page {i + 1}")
                self.thumb_list.addItem(item)
            self.thumb_list.setCurrentRow(self.current_page)
        self.thumb_list.blockSignals(False)

    def refresh_view(self):
        if not self.engine.doc:
            return
        total = len(self.engine.doc)
        self.current_page = max(0, min(self.current_page, total - 1))
        self.canvas.page_num = self.current_page
        self.canvas.search_rects = self.search_hits.get(self.current_page, [])

        png_data = self.engine.render_page(self.current_page)
        if png_data:
            pix = QPixmap()
            pix.loadFromData(png_data)
            self.canvas.setPixmap(pix)
            self.canvas.setFixedSize(pix.size())

        self.page_label.setText(f" Page {self.current_page + 1} / {total} ")
        self.thumb_list.blockSignals(True)
        self.thumb_list.setCurrentRow(self.current_page)
        self.thumb_list.blockSignals(False)

    def _on_thumbnail_selected(self, row: int):
        if row >= 0:
            self.go_to_page(row)

    def go_to_page(self, page_num: int):
        if self.engine.doc and 0 <= page_num < len(self.engine.doc):
            self.current_page = page_num
            self.refresh_view()

    def change_zoom(self, delta: float):
        self.engine.zoom = max(0.4, min(4.0, self.engine.zoom + delta))
        self.refresh_view()

    def toggle_dark_mode(self):
        self.engine.dark_mode = not self.engine.dark_mode
        self.refresh_view()

    def set_mode(self, mode: str):
        self.canvas.tool_mode = mode
        cursors = {
            "pan": Qt.ArrowCursor,
            "highlight": Qt.CrossCursor,
            "note": Qt.PointingHandCursor,
            "text": Qt.IBeamCursor,
            "ink": Qt.CrossCursor,
            "sign": Qt.PointingHandCursor,
        }
        self.canvas.setCursor(cursors.get(mode, Qt.ArrowCursor))

    def run_search(self):
        query = self.search_input.text().strip()
        self.search_hits = self.engine.search_text(query)
        if not self.search_hits:
            QMessageBox.information(self, "Search", "No matches found.")
        else:
            first_page = sorted(self.search_hits.keys())[0]
            total_matches = sum(len(v) for v in self.search_hits.values())
            self.go_to_page(first_page)
            QMessageBox.information(
                self, "Search", f"Found {total_matches} matches across {len(self.search_hits)} pages."
            )

    # -------------------- ANNOTATIONS & SIGNING --------------------

    def start_sign_mode(self):
        if not self.engine.doc:
            return
        dlg = SignatureDialog(self)
        if dlg.exec() and dlg.sig_path:
            self.canvas.pending_sig_path = dlg.sig_path
            self.set_mode("sign")
            QMessageBox.information(self, "Place Signature", "Click anywhere on the page to stamp your signature.")

    def clear_annots(self):
        if self.engine.doc:
            self.engine.clear_page_annotations(self.current_page)
            self.refresh_view()

    def save_file(self):
        if self.engine.doc:
            self.engine.save_pdf()
            QMessageBox.information(self, "Saved", "PDF saved successfully.")

    def save_file_as(self):
        if not self.engine.doc:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save PDF As", "", "PDF Files (*.pdf)")
        if out:
            self.engine.save_pdf(out)
            QMessageBox.information(self, "Saved", f"Saved to {out}")

    # -------------------- PRO TOOLS --------------------

    def rotate_current_page(self):
        if self.engine.doc:
            self.engine.rotate_page(self.current_page, 90)
            self.populate_thumbnails()
            self.refresh_view()

    def move_current_page(self, offset: int):
        if not self.engine.doc:
            return
        target = self.current_page + offset
        if 0 <= target < len(self.engine.doc):
            self.engine.move_page(self.current_page, target)
            self.current_page = target
            self.populate_thumbnails()
            self.refresh_view()

    def delete_current_page(self):
        if self.engine.doc and len(self.engine.doc) > 1:
            self.engine.delete_page(self.current_page)
            self.populate_thumbnails()
            self.refresh_view()

    def merge_pdfs(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select PDFs to Merge", "", "PDF Files (*.pdf)")
        if len(files) < 2:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save Merged PDF", "merged.pdf", "PDF Files (*.pdf)")
        if out:
            self.engine.merge_pdfs(files, out)
            QMessageBox.information(self, "Merged", f"Merged {len(files)} PDFs into {out}")

    def split_pdf(self):
        if not self.engine.doc:
            return
        dlg = SplitDialog(len(self.engine.doc), self)
        if dlg.exec():
            out, _ = QFileDialog.getSaveFileName(self, "Save Extracted PDF", "extracted.pdf", "PDF Files (*.pdf)")
            if out:
                self.engine.split_pdf(dlg.start_spin.value(), dlg.end_spin.value(), out)
                QMessageBox.information(self, "Extracted", f"Saved extracted pages to {out}")

    def export_word(self):
        if not self.engine.doc:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Convert PDF to Word", "converted.docx", "Word Document (*.docx)")
        if out:
            self.engine.pdf_to_word(out)
            QMessageBox.information(self, "Converted", f"Exported Word file to {out}")

    def export_images(self):
        if not self.engine.doc:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder for PNGs")
        if folder:
            self.engine.pdf_to_images(folder)
            QMessageBox.information(self, "Exported", f"All pages saved as PNG images in {folder}")

    def create_pdf_from_images(self):
        imgs, _ = QFileDialog.getOpenFileNames(
            self, "Select Images", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not imgs:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save Created PDF", "from_images.pdf", "PDF Files (*.pdf)")
        if out:
            self.engine.images_to_pdf(imgs, out)
            QMessageBox.information(self, "Created", f"Created PDF at {out}")

    def compress_pdf(self):
        if not self.engine.doc:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save Compressed PDF", "compressed.pdf", "PDF Files (*.pdf)")
        if out:
            self.engine.compress_pdf(out)
            QMessageBox.information(self, "Compressed", f"Compressed PDF saved to {out}")

    def encrypt_pdf(self):
        if not self.engine.doc:
            return
        dlg = EncryptDialog(self)
        if dlg.exec() and dlg.user_pw.text():
            out, _ = QFileDialog.getSaveFileName(self, "Save Protected PDF", "protected.pdf", "PDF Files (*.pdf)")
            if out:
                self.engine.encrypt_pdf(out, dlg.user_pw.text(), dlg.owner_pw.text())
                QMessageBox.information(self, "Protected", f"AES-256 encrypted PDF saved to {out}")

    def read_aloud(self):
        if self.engine.doc:
            self.engine.read_aloud(self.current_page)

    def show_ocr_text(self):
        if not self.engine.doc:
            return
        text = self.engine.extract_text_or_ocr(self.current_page)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Extracted / OCR Text - Page {self.current_page + 1}")
        dlg.resize(600, 450)
        layout = QVBoxLayout(dlg)
        box = QTextEdit()
        box.setPlainText(text)
        layout.addWidget(box)
        dlg.exec()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OpenAcrobatWindow()
    window.show()
    sys.exit(app.exec())
