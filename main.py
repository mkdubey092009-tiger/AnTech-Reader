import io
import os
import time
import traceback
from PIL import Image as PILImage

try:
    from pypdf import PdfReader, PdfWriter
    PYPDF_OK = True
    PYPDF_ERR = ""
except Exception as _imp_err:
    PYPDF_OK = False
    PYPDF_ERR = traceback.format_exc()

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.graphics import Color, Line, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

REQ_OPEN_PDF = 1001
REQ_MERGE_PDFS = 1002
REQ_IMAGES_TO_PDF = 1003

if platform == "android":
    from android import activity
    from android.permissions import Permission, request_permissions
    from jnius import autoclass

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Intent = autoclass("android.content.Intent")
    File = autoclass("java.io.File")
    FileOutputStream = autoclass("java.io.FileOutputStream")
    ParcelFileDescriptor = autoclass("android.os.ParcelFileDescriptor")
    PdfRenderer = autoclass("android.graphics.pdf.PdfRenderer")
    PdfDocument = autoclass("android.graphics.pdf.PdfDocument")
    PageInfoBuilder = autoclass("android.graphics.pdf.PdfDocument$PageInfo$Builder")
    Bitmap = autoclass("android.graphics.Bitmap")
    BitmapConfig = autoclass("android.graphics.Bitmap$Config")
    BitmapFactory = autoclass("android.graphics.BitmapFactory")
    Canvas = autoclass("android.graphics.Canvas")
    Paint = autoclass("android.graphics.Paint")
    PaintStyle = autoclass("android.graphics.Paint$Style")
    RectF = autoclass("android.graphics.RectF")
    AndroidColor = autoclass("android.graphics.Color")
    ByteArrayOutputStream = autoclass("java.io.ByteArrayOutputStream")
    CompressFormat = autoclass("android.graphics.Bitmap$CompressFormat")
    Environment = autoclass("android.os.Environment")
    TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
    Locale = autoclass("java.util.Locale")


class SignaturePad(Widget):
    """Touch pad for drawing signatures on Android."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.strokes = []
        self.current_line = None
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self.bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, *_):
        self.bg.pos = self.pos
        self.bg.size = self.size

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        with self.canvas:
            Color(0.06, 0.09, 0.16, 1)
            self.current_line = Line(points=[touch.x, touch.y], width=2.5)
        self.strokes.append(self.current_line)
        return True

    def on_touch_move(self, touch):
        if self.current_line and self.collide_point(*touch.pos):
            self.current_line.points += [touch.x, touch.y]
            return True
        return False

    def clear_pad(self):
        self.canvas.clear()
        self.strokes.clear()
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self.bg = Rectangle(pos=self.pos, size=self.size)


class PDFPageCanvas(Image):
    """Interactive PDF page view supporting Highlight, Text, Ink Draw, and Signature placement."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app_ref
        self.allow_stretch = True
        self.keep_ratio = True
        self.start_pt = None
        self.ink_pts = []
        self.preview_line = None
        self.preview_rect = None

    def _widget_to_norm(self, x, y):
        if self.width <= 0 or self.height <= 0:
            return 0.0, 0.0
        nx = max(0.0, min(1.0, (x - self.x) / self.width))
        ny = max(0.0, min(1.0, 1.0 - ((y - self.y) / self.height)))
        return nx, ny

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        mode = self.app_ref.tool_mode
        if mode == "pan":
            return super().on_touch_down(touch)

        nx, ny = self._widget_to_norm(touch.x, touch.y)

        if mode == "text":
            self.app_ref.prompt_add_text(nx, ny)
            return True

        if mode == "sign" and self.app_ref.sig_png_path:
            self.app_ref.apply_signature_at(nx, ny)
            return True

        self.start_pt = (touch.x, touch.y)
        self.ink_pts = [(nx, ny)]

        if mode == "draw":
            with self.canvas.after:
                Color(0.86, 0.15, 0.15, 1)
                self.preview_line = Line(points=[touch.x, touch.y], width=2.2)
        elif mode == "highlight":
            with self.canvas.after:
                Color(1, 0.92, 0.23, 0.45)
                self.preview_rect = Rectangle(pos=(touch.x, touch.y), size=(1, 1))
        return True

    def on_touch_move(self, touch):
        if not self.start_pt:
            return super().on_touch_move(touch)

        mode = self.app_ref.tool_mode
        nx, ny = self._widget_to_norm(touch.x, touch.y)

        if mode == "draw" and self.preview_line:
            self.preview_line.points += [touch.x, touch.y]
            self.ink_pts.append((nx, ny))
        elif mode == "highlight" and self.preview_rect:
            x0, y0 = self.start_pt
            self.preview_rect.pos = (min(x0, touch.x), min(y0, touch.y))
            self.preview_rect.size = (abs(touch.x - x0), abs(touch.y - y0))
        return True

    def on_touch_up(self, touch):
        if not self.start_pt:
            return super().on_touch_up(touch)

        mode = self.app_ref.tool_mode
        self.canvas.after.clear()

        if mode == "draw" and len(self.ink_pts) > 1:
            self.app_ref.apply_ink_strokes(self.ink_pts)
        elif mode == "highlight":
            nx1, ny1 = self._widget_to_norm(*self.start_pt)
            nx2, ny2 = self._widget_to_norm(touch.x, touch.y)
            self.app_ref.apply_highlight_box(nx1, ny1, nx2, ny2)

        self.start_pt = None
        self.ink_pts = []
        self.preview_line = None
        self.preview_rect = None
        return True


class AnTechReaderApp(App):
    def build(self):
        self.title = "AnTech Reader"
        self.icon = "icon.png"
        self.current_pdf = None
        self.current_page = 0
        self.total_pages = 0
        self.zoom = 1.5
        self.dark_mode = False
        self.tool_mode = "pan"
        self.sig_png_path = None
        self.tts = None

        if not PYPDF_OK:
            return self._build_error_screen(f"PDF Library Error:\n{PYPDF_ERR}")

        try:
            if platform == "android":
                try:
                    request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
                except Exception:
                    pass
                try:
                    activity.bind(on_activity_result=self.on_activity_result)
                except Exception:
                    pass
                try:
                    self.tts = TextToSpeech(PythonActivity.mActivity, None)
                except Exception:
                    self.tts = None

            root = BoxLayout(orientation="vertical", spacing=4, padding=6)

            # Top Action Bar
            top_bar = GridLayout(cols=6, size_hint_y=None, height=46, spacing=4)
            top_bar.add_widget(Button(text="Open", background_color=(0.15, 0.39, 0.92, 1), on_release=lambda *_: self.pick_single_pdf()))
            top_bar.add_widget(Button(text="Save", on_release=lambda *_: self.save_to_downloads()))
            top_bar.add_widget(Button(text="Prev", on_release=lambda *_: self.change_page(-1)))
            self.page_lbl = Label(text="1 / 1", size_hint_x=0.8)
            top_bar.add_widget(self.page_lbl)
            top_bar.add_widget(Button(text="Next", on_release=lambda *_: self.change_page(1)))
            top_bar.add_widget(Button(text="Dark", on_release=lambda *_: self.toggle_dark_mode()))
            root.add_widget(top_bar)

            # Scrollable Tool Strip
            tool_scroll = ScrollView(size_hint_y=None, height=46, do_scroll_y=False)
            tool_bar = BoxLayout(orientation="horizontal", size_hint_x=None, spacing=4)
            tool_bar.bind(minimum_width=tool_bar.setter("width"))

            tools = [
                ("Pan", lambda *_: self.set_mode("pan")),
                ("Highlight", lambda *_: self.set_mode("highlight")),
                ("Add Text", lambda *_: self.set_mode("text")),
                ("Draw", lambda *_: self.set_mode("draw")),
                ("Fill & Sign", lambda *_: self.open_signature_dialog()),
                ("Zoom +", lambda *_: self.change_zoom(0.3)),
                ("Zoom -", lambda *_: self.change_zoom(-0.3)),
                ("Rotate 90", lambda *_: self.rotate_page()),
                ("Delete Pg", lambda *_: self.delete_page()),
                ("Merge PDFs", lambda *_: self.pick_merge_pdfs()),
                ("Split PDF", lambda *_: self.open_split_dialog()),
                ("Img -> PDF", lambda *_: self.pick_images_to_pdf()),
                ("Protect", lambda *_: self.open_encrypt_dialog()),
                ("Read Aloud", lambda *_: self.read_aloud()),
                ("Search", lambda *_: self.open_search_dialog()),
            ]
            for label, cb in tools:
                btn = Button(text=label, size_hint_x=None, width=96)
                btn.bind(on_release=cb)
                tool_bar.add_widget(btn)

            tool_scroll.add_widget(tool_bar)
            root.add_widget(tool_scroll)

            # Center PDF Viewport
            self.viewer_scroll = ScrollView()
            self.pdf_view = PDFPageCanvas(self, size_hint=(None, None))
            self.viewer_scroll.add_widget(self.pdf_view)
            root.add_widget(self.viewer_scroll)

            # Status Bar
            self.status_lbl = Label(text="AnTech Reader - Tap Open to load any PDF", size_hint_y=None, height=28, font_size="12sp")
            root.add_widget(self.status_lbl)

            Clock.schedule_once(lambda *_: self._create_welcome_pdf(), 0.3)
            return root
        except Exception:
            return self._build_error_screen(traceback.format_exc())

    def _build_error_screen(self, err_text):
        box = BoxLayout(orientation="vertical", padding=12, spacing=8)
        box.add_widget(Label(text="Startup Error Details:", size_hint_y=None, height=32))
        ti = TextInput(text=err_text, readonly=True)
        box.add_widget(ti)
        return box

    def _create_welcome_pdf(self):
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            sample_path = os.path.join(self.user_data_dir, "welcome.pdf")
            if platform == "android":
                doc = PdfDocument()
                page_info = PageInfoBuilder(595, 842, 1).create()
                page = doc.startPage(page_info)
                canvas = page.getCanvas()
                canvas.drawColor(AndroidColor.WHITE)

                title_paint = Paint()
                title_paint.setColor(AndroidColor.rgb(37, 99, 235))
                title_paint.setTextSize(28.0)
                title_paint.setAntiAlias(True)
                canvas.drawText("AnTech Reader", 45.0, 80.0, title_paint)

                body_paint = Paint()
                body_paint.setColor(AndroidColor.rgb(30, 41, 59))
                body_paint.setTextSize(15.0)
                body_paint.setAntiAlias(True)
                lines = [
                    "Welcome to AnTech Reader!",
                    "1. Tap 'Open' at top-left to load any PDF from your phone.",
                    "2. Swipe the toolbar to Highlight, Add Text, Draw, or Fill & Sign.",
                    "3. Rotate, Delete, Merge, Split, Protect, or Read Aloud any PDF.",
                ]
                y = 130.0
                for line in lines:
                    canvas.drawText(line, 45.0, y, body_paint)
                    y += 30.0

                doc.finishPage(page)
                fos = FileOutputStream(sample_path)
                doc.writeTo(fos)
                fos.close()
                doc.close()
                self.load_pdf(sample_path)
        except Exception as e:
            self.status_lbl.text = f"Ready (Tap Open to select PDF): {e}"

    # -------------------- RENDERING --------------------

    def load_pdf(self, path):
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                self.prompt_password_and_decrypt(path)
                return
            self.current_pdf = path
            self.total_pages = len(reader.pages)
            self.current_page = max(0, min(self.current_page, self.total_pages - 1))
            self.render_current_page()
        except Exception as e:
            self.status_lbl.text = f"Error: {e}"

    def render_current_page(self):
        if not self.current_pdf:
            return
        self.page_lbl.text = f"{self.current_page + 1} / {self.total_pages}"

        if platform == "android":
            try:
                java_file = File(self.current_pdf)
                pfd = ParcelFileDescriptor.open(java_file, ParcelFileDescriptor.MODE_READ_ONLY)
                renderer = PdfRenderer(pfd)
                page = renderer.openPage(self.current_page)
                w = max(200, int(page.getWidth() * self.zoom))
                h = max(200, int(page.getHeight() * self.zoom))
                bitmap = Bitmap.createBitmap(w, h, BitmapConfig.ARGB_8888)
                canvas = Canvas(bitmap)
                canvas.drawColor(AndroidColor.WHITE)
                page.render(bitmap, None, None, 1)
                page.close()
                renderer.close()
                pfd.close()

                baos = ByteArrayOutputStream()
                bitmap.compress(CompressFormat.PNG, 100, baos)
                png_bytes = bytes(b & 0xFF for b in baos.toByteArray())

                if self.dark_mode:
                    im = PILImage.open(io.BytesIO(png_bytes)).convert("RGB")
                    im = PILImage.eval(im, lambda px: 255 - px)
                    out_buf = io.BytesIO()
                    im.save(out_buf, format="PNG")
                    png_bytes = out_buf.getvalue()

                tex = CoreImage(io.BytesIO(png_bytes), ext="png").texture
                self.pdf_view.texture = tex
                self.pdf_view.size = (w, h)
            except Exception as e:
                self.status_lbl.text = f"Render error: {e}"
        else:
            self.pdf_view.size = (600, 800)

    def change_page(self, delta):
        if not self.current_pdf:
            return
        new_p = self.current_page + delta
        if 0 <= new_p < self.total_pages:
            self.current_page = new_p
            self.render_current_page()

    def change_zoom(self, delta):
        self.zoom = max(0.7, min(3.5, self.zoom + delta))
        self.render_current_page()

    def toggle_dark_mode(self):
        self.dark_mode = not self.dark_mode
        self.render_current_page()

    def set_mode(self, mode):
        self.tool_mode = mode
        self.status_lbl.text = f"Active tool: {mode.upper()}"

    # -------------------- NATIVE ANDROID ANNOTATIONS & SIGNING --------------------

    def _merge_native_overlay(self, draw_callback):
        if not self.current_pdf or platform != "android":
            return
        try:
            reader = PdfReader(self.current_pdf)
            page = reader.pages[self.current_page]
            pw = max(100, int(float(page.mediabox.width)))
            ph = max(100, int(float(page.mediabox.height)))

            overlay_pdf = os.path.join(self.user_data_dir, "overlay.pdf")
            doc = PdfDocument()
            page_info = PageInfoBuilder(pw, ph, 1).create()
            pdf_page = doc.startPage(page_info)
            canvas = pdf_page.getCanvas()
            draw_callback(canvas, float(pw), float(ph))
            doc.finishPage(pdf_page)
            fos = FileOutputStream(overlay_pdf)
            doc.writeTo(fos)
            fos.close()
            doc.close()

            overlay_reader = PdfReader(overlay_pdf)
            writer = PdfWriter()
            for idx, p in enumerate(reader.pages):
                if idx == self.current_page:
                    p.merge_page(overlay_reader.pages[0])
                writer.add_page(p)

            updated_path = os.path.join(self.user_data_dir, f"edit_{int(time.time() * 1000)}.pdf")
            with open(updated_path, "wb") as f:
                writer.write(f)
            self.load_pdf(updated_path)
        except Exception as e:
            self.status_lbl.text = f"Edit error: {e}"

    def apply_highlight_box(self, nx1, ny1, nx2, ny2):
        def _draw(canvas, pw, ph):
            x0, x1 = sorted([nx1 * pw, nx2 * pw])
            y0, y1 = sorted([ny1 * ph, ny2 * ph])
            if (x1 - x0) > 3 and (y1 - y0) > 3:
                paint = Paint()
                paint.setColor(AndroidColor.argb(115, 255, 235, 59))
                paint.setStyle(PaintStyle.FILL)
                canvas.drawRect(float(x0), float(y0), float(x1), float(y1), paint)

        self._merge_native_overlay(_draw)
        self.status_lbl.text = "Highlight added"

    def apply_ink_strokes(self, norm_pts):
        def _draw(canvas, pw, ph):
            paint = Paint()
            paint.setColor(AndroidColor.rgb(220, 38, 38))
            paint.setStrokeWidth(2.5)
            paint.setAntiAlias(True)
            for i in range(1, len(norm_pts)):
                x1, y1 = norm_pts[i - 1][0] * pw, norm_pts[i - 1][1] * ph
                x2, y2 = norm_pts[i][0] * pw, norm_pts[i][1] * ph
                canvas.drawLine(float(x1), float(y1), float(x2), float(y2), paint)

        self._merge_native_overlay(_draw)
        self.status_lbl.text = "Drawing added"

    def prompt_add_text(self, nx, ny):
        box = BoxLayout(orientation="vertical", spacing=8, padding=10)
        inp = TextInput(hint_text="Type text to insert...", multiline=False)
        btn = Button(text="Insert Text", size_hint_y=None, height=44)
        box.add_widget(inp)
        box.add_widget(btn)
        pop = Popup(title="Add Text", content=box, size_hint=(0.85, 0.32))

        def _insert(*_):
            txt = inp.text.strip()
            pop.dismiss()
            if txt:
                def _draw(canvas, pw, ph):
                    paint = Paint()
                    paint.setColor(AndroidColor.rgb(15, 45, 180))
                    paint.setTextSize(16.0)
                    paint.setAntiAlias(True)
                    canvas.drawText(txt, float(nx * pw), float(ny * ph), paint)

                self._merge_native_overlay(_draw)
                self.status_lbl.text = "Text inserted"

        btn.bind(on_release=_insert)
        pop.open()

    def open_signature_dialog(self):
        box = BoxLayout(orientation="vertical", spacing=8, padding=10)
        pad = SignaturePad(size_hint_y=1)
        row = BoxLayout(size_hint_y=None, height=44, spacing=8)
        btn_clear = Button(text="Clear")
        btn_use = Button(text="Use Signature", background_color=(0.15, 0.39, 0.92, 1))
        row.add_widget(btn_clear)
        row.add_widget(btn_use)
        box.add_widget(pad)
        box.add_widget(row)

        pop = Popup(title="Draw Signature", content=box, size_hint=(0.9, 0.5))
        btn_clear.bind(on_release=lambda *_: pad.clear_pad())

        def _save_sig(*_):
            sig_file = os.path.join(self.user_data_dir, "signature.png")
            pad.export_to_png(sig_file)
            self.sig_png_path = sig_file
            pop.dismiss()
            self.set_mode("sign")
            self.status_lbl.text = "Tap anywhere on the PDF to stamp signature"

        btn_use.bind(on_release=_save_sig)
        pop.open()

    def apply_signature_at(self, nx, ny):
        def _draw(canvas, pw, ph):
            bmp = BitmapFactory.decodeFile(self.sig_png_path)
            if bmp is not None:
                x = float(nx * pw)
                y = float(ny * ph)
                dest = RectF(x, y, x + 130.0, y + 55.0)
                canvas.drawBitmap(bmp, None, dest, None)

        self._merge_native_overlay(_draw)
        self.set_mode("pan")
        self.status_lbl.text = "Signature stamped"

    # -------------------- ORGANIZE, PROTECT, SEARCH, TTS --------------------

    def rotate_page(self):
        if not self.current_pdf:
            return
        reader = PdfReader(self.current_pdf)
        writer = PdfWriter()
        for i, p in enumerate(reader.pages):
            if i == self.current_page:
                p.rotate(90)
            writer.add_page(p)
        out = os.path.join(self.user_data_dir, f"rot_{int(time.time() * 1000)}.pdf")
        with open(out, "wb") as f:
            writer.write(f)
        self.load_pdf(out)

    def delete_page(self):
        if not self.current_pdf or self.total_pages <= 1:
            return
        reader = PdfReader(self.current_pdf)
        writer = PdfWriter()
        for i, p in enumerate(reader.pages):
            if i != self.current_page:
                writer.add_page(p)
        out = os.path.join(self.user_data_dir, f"del_{int(time.time() * 1000)}.pdf")
        with open(out, "wb") as f:
            writer.write(f)
        self.load_pdf(out)

    def open_split_dialog(self):
        if not self.current_pdf:
            return
        box = BoxLayout(orientation="vertical", spacing=8, padding=10)
        from_in = TextInput(text="1", hint_text="From page", input_filter="int", multiline=False)
        to_in = TextInput(text=str(self.total_pages), hint_text="To page", input_filter="int", multiline=False)
        btn = Button(text="Extract Range", size_hint_y=None, height=44)
        box.add_widget(from_in)
        box.add_widget(to_in)
        box.add_widget(btn)
        pop = Popup(title="Split / Extract Pages", content=box, size_hint=(0.85, 0.4))

        def _split(*_):
            s = max(1, int(from_in.text or 1))
            e = min(self.total_pages, int(to_in.text or self.total_pages))
            pop.dismiss()
            reader = PdfReader(self.current_pdf)
            writer = PdfWriter()
            for i in range(s - 1, e):
                writer.add_page(reader.pages[i])
            out = os.path.join(self.user_data_dir, f"split_{int(time.time() * 1000)}.pdf")
            with open(out, "wb") as f:
                writer.write(f)
            self.current_page = 0
            self.load_pdf(out)
            self.status_lbl.text = f"Extracted pages {s}-{e}"

        btn.bind(on_release=_split)
        pop.open()

    def open_encrypt_dialog(self):
        if not self.current_pdf:
            return
        box = BoxLayout(orientation="vertical", spacing=8, padding=10)
        pw_in = TextInput(hint_text="Set password...", password=True, multiline=False)
        btn = Button(text="Encrypt & Save to Downloads", size_hint_y=None, height=44)
        box.add_widget(pw_in)
        box.add_widget(btn)
        pop = Popup(title="Password Protect PDF", content=box, size_hint=(0.85, 0.34))

        def _lock(*_):
            pw = pw_in.text.strip()
            pop.dismiss()
            if pw:
                reader = PdfReader(self.current_pdf)
                writer = PdfWriter()
                for p in reader.pages:
                    writer.add_page(p)
                writer.encrypt(pw)
                out = self._get_save_path("AnTech_Reader_Protected.pdf")
                with open(out, "wb") as f:
                    writer.write(f)
                self.status_lbl.text = f"Saved protected PDF: {out}"

        btn.bind(on_release=_lock)
        pop.open()

    def prompt_password_and_decrypt(self, path):
        box = BoxLayout(orientation="vertical", spacing=8, padding=10)
        pw_in = TextInput(hint_text="Enter PDF password...", password=True, multiline=False)
        btn = Button(text="Unlock", size_hint_y=None, height=44)
        box.add_widget(pw_in)
        box.add_widget(btn)
        pop = Popup(title="Encrypted PDF", content=box, size_hint=(0.85, 0.34))

        def _unlock(*_):
            pop.dismiss()
            reader = PdfReader(path)
            if reader.decrypt(pw_in.text):
                writer = PdfWriter()
                for p in reader.pages:
                    writer.add_page(p)
                dec_path = os.path.join(self.user_data_dir, "unlocked.pdf")
                with open(dec_path, "wb") as f:
                    writer.write(f)
                self.load_pdf(dec_path)
            else:
                self.status_lbl.text = "Incorrect password"

        btn.bind(on_release=_unlock)
        pop.open()

    def open_search_dialog(self):
        if not self.current_pdf:
            return
        box = BoxLayout(orientation="vertical", spacing=8, padding=10)
        q_in = TextInput(hint_text="Search word...", multiline=False)
        btn = Button(text="Find", size_hint_y=None, height=44)
        box.add_widget(q_in)
        box.add_widget(btn)
        pop = Popup(title="Search PDF", content=box, size_hint=(0.85, 0.34))

        def _find(*_):
            q = q_in.text.strip().lower()
            pop.dismiss()
            if not q:
                return
            reader = PdfReader(self.current_pdf)
            for idx, page in enumerate(reader.pages):
                txt = (page.extract_text() or "").lower()
                if q in txt:
                    self.current_page = idx
                    self.render_current_page()
                    self.status_lbl.text = f"Found '{q}' on page {idx + 1}"
                    return
            self.status_lbl.text = "No matches found"

        btn.bind(on_release=_find)
        pop.open()

    def read_aloud(self):
        if not self.current_pdf:
            return
        reader = PdfReader(self.current_pdf)
        text = (reader.pages[self.current_page].extract_text() or "").strip()
        if not text:
            self.status_lbl.text = "No selectable text on this page"
            return
        if platform == "android" and self.tts:
            try:
                self.tts.setLanguage(Locale.US)
                self.tts.speak(text[:3500], 0, None, "antech_reader_tts")
                self.status_lbl.text = "Reading page aloud..."
            except Exception as e:
                self.status_lbl.text = f"TTS error: {e}"

    # -------------------- ANDROID FILE PICKER & SAVING --------------------

    def _launch_picker(self, mime_type, req_code, multiple=False):
        if platform != "android":
            self.status_lbl.text = "Android file picker works on Android devices"
            return
        intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        intent.setType(mime_type)
        if multiple:
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)
        PythonActivity.mActivity.startActivityForResult(intent, req_code)

    def pick_single_pdf(self):
        self._launch_picker("application/pdf", REQ_OPEN_PDF, multiple=False)

    def pick_merge_pdfs(self):
        self._launch_picker("application/pdf", REQ_MERGE_PDFS, multiple=True)

    def pick_images_to_pdf(self):
        self._launch_picker("image/*", REQ_IMAGES_TO_PDF, multiple=True)

    def _copy_uri_to_local(self, uri, filename):
        resolver = PythonActivity.mActivity.getContentResolver()
        stream = resolver.openInputStream(uri)
        dest = os.path.join(self.user_data_dir, filename)
        buf = bytearray(8192)
        with open(dest, "wb") as out:
            while True:
                n = stream.read(buf)
                if n == -1:
                    break
                out.write(buf[:n])
        stream.close()
        return dest

    def on_activity_result(self, request_code, result_code, intent):
        if result_code != -1 or intent is None:
            return

        uris = []
        clip = intent.getClipData()
        if clip is not None:
            for i in range(clip.getItemCount()):
                uris.append(clip.getItemAt(i).getUri())
        elif intent.getData() is not None:
            uris.append(intent.getData())

        if not uris:
            return

        if request_code == REQ_OPEN_PDF:
            local_pdf = self._copy_uri_to_local(uris[0], "opened.pdf")
            Clock.schedule_once(lambda *_: self.load_pdf(local_pdf), 0)

        elif request_code == REQ_MERGE_PDFS:
            writer = PdfWriter()
            for idx, u in enumerate(uris):
                part = self._copy_uri_to_local(u, f"merge_{idx}.pdf")
                reader = PdfReader(part)
                for p in reader.pages:
                    writer.add_page(p)
            merged_out = os.path.join(self.user_data_dir, "merged.pdf")
            with open(merged_out, "wb") as f:
                writer.write(f)
            Clock.schedule_once(lambda *_: self.load_pdf(merged_out), 0)

        elif request_code == REQ_IMAGES_TO_PDF:
            doc = PdfDocument()
            for idx, u in enumerate(uris):
                img_path = self._copy_uri_to_local(u, f"img_{idx}.png")
                bmp = BitmapFactory.decodeFile(img_path)
                if bmp is not None:
                    w, h = bmp.getWidth(), bmp.getHeight()
                    p_info = PageInfoBuilder(w, h, idx + 1).create()
                    p = doc.startPage(p_info)
                    p.getCanvas().drawBitmap(bmp, 0.0, 0.0, None)
                    doc.finishPage(p)
            out_pdf = os.path.join(self.user_data_dir, "from_images.pdf")
            fos = FileOutputStream(out_pdf)
            doc.writeTo(fos)
            fos.close()
            doc.close()
            Clock.schedule_once(lambda *_: self.load_pdf(out_pdf), 0)

    def _get_save_path(self, default_name):
        if platform == "android":
            dl_dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS).getAbsolutePath()
            if os.path.isdir(dl_dir):
                return os.path.join(dl_dir, default_name)
        return os.path.join(self.user_data_dir, default_name)

    def save_to_downloads(self):
        if not self.current_pdf:
            return
        dest = self._get_save_path(f"AnTech_Reader_{int(time.time())}.pdf")
        with open(self.current_pdf, "rb") as src, open(dest, "wb") as dst:
            dst.write(src.read())
        self.status_lbl.text = f"Saved: {dest}"


if __name__ == "__main__":
    AnTechReaderApp().run()
