import os
import tempfile
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPainter, QPen, QImage, QColor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QSpinBox,
    QFileDialog,
    QFormLayout,
)


class SignatureCanvas(QLabel):
    """Smooth freehand pad for drawing signatures."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(460, 180)
        self.image = QImage(460, 180, QImage.Format_ARGB32)
        self.image.fill(Qt.transparent)
        self.last_point = QPoint()
        self.drawing = False
        self.setStyleSheet("border: 2px dashed #64748b; background: #ffffff;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = True
            self.last_point = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.drawing and (event.buttons() & Qt.LeftButton):
            painter = QPainter(self.image)
            painter.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor("#0f172a"), 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            curr = event.position().toPoint()
            painter.drawLine(self.last_point, curr)
            self.last_point = curr
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = False

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.drawImage(0, 0, self.image)

    def clear(self):
        self.image.fill(Qt.transparent)
        self.update()


class SignatureDialog(QDialog):
    """Dialog allowing users to draw a signature or upload a signature PNG."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fill & Sign - Create Signature")
        self.sig_path: str | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Draw your signature below or upload an existing image:"))

        self.pad = SignatureCanvas()
        layout.addWidget(self.pad)

        btn_row = QHBoxLayout()
        btn_clear = QPushButton("Clear")
        btn_upload = QPushButton("Upload Image...")
        btn_apply = QPushButton("Use Signature")
        btn_apply.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold;")

        btn_clear.clicked.connect(self.pad.clear)
        btn_upload.clicked.connect(self._upload_image)
        btn_apply.clicked.connect(self._save_drawn)

        btn_row.addWidget(btn_clear)
        btn_row.addWidget(btn_upload)
        btn_row.addStretch()
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

    def _upload_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Signature Image", "", "Images (*.png *.jpg *.jpeg)"
        )
        if path:
            self.sig_path = path
            self.accept()

    def _save_drawn(self):
        fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        self.pad.image.save(temp_path, "PNG")
        self.sig_path = temp_path
        self.accept()


class SplitDialog(QDialog):
    """Dialog to choose start and end pages for splitting/extracting."""

    def __init__(self, max_pages: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Split / Extract Pages")
        form = QFormLayout(self)

        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, max_pages)
        self.start_spin.setValue(1)

        self.end_spin = QSpinBox()
        self.end_spin.setRange(1, max_pages)
        self.end_spin.setValue(max_pages)

        form.addRow("From Page:", self.start_spin)
        form.addRow("To Page:", self.end_spin)

        btn_ok = QPushButton("Extract Range")
        btn_ok.clicked.connect(self.accept)
        form.addRow(btn_ok)


class EncryptDialog(QDialog):
    """Dialog to set User and Owner passwords for PDF encryption."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Password Protect PDF (AES-256)")
        form = QFormLayout(self)

        self.user_pw = QLineEdit()
        self.user_pw.setEchoMode(QLineEdit.Password)
        self.owner_pw = QLineEdit()
        self.owner_pw.setEchoMode(QLineEdit.Password)

        form.addRow("Open Password (User):", self.user_pw)
        form.addRow("Permissions Password (Owner):", self.owner_pw)

        btn_ok = QPushButton("Encrypt & Save")
        btn_ok.clicked.connect(self.accept)
        form.addRow(btn_ok)
