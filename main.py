import io
import json
import os
import time
import traceback

from kivy.config import Config
Config.set("graphics", "multisamples", "0")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen, ScreenManager, SlideTransition
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

try:
    from PIL import Image as PILImage
    from pypdf import PdfReader, PdfWriter
    LIBS_OK = True
    LIBS_ERR = ""
except Exception:
    LIBS_OK = False
    LIBS_ERR = traceback.format_exc()

REQ_OPEN_PDF = 1001
REQ_MERGE_PDFS = 1002
REQ_IMAGES_TO_PDF = 1003
REQ_SAVE_PDF = 1004

RED_PRIMARY = (0.83, 0.18, 0.18, 1)      # Acrobat Red (#D32F2F)
RED_DARK = (0.71, 0.11, 0.11, 1)
BG_LIGHT = (0.96, 0.97, 0.98, 1)         # Clean off-white home background
CANVAS_BG = (0.89, 0.91, 0.94, 1)        # Soft gray reader backdrop
CARD_WHITE = (1, 1, 1, 1)
TEXT_DARK = (0.12, 0.16, 0.23, 1)
TEXT_MUTED = (0.42, 0.47, 0.55, 1)


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf_objects(filepath: str, objs: list[bytes]):
    """Writes a valid PDF 1.4 file with an exact byte-offset xref table."""
    header = b"%PDF-1.4\n"
    offsets = []
    body = bytearray(header)
    for obj in objs:
        offsets.append(len(body))
        body.extend(obj)

    xref_pos = len(body)
    body.extend(f"xref\n0 {len(objs) + 1}\n".encode("latin-1"))
    body.extend(b"0000000000 65535 f \n")
    for off in offsets:
        body.extend(f"{off:010d} 00000 n \n".encode("latin-1"))

    trailer = (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode("latin-1")
    body.extend(trailer)

    with open(filepath, "wb") as f:
        f.write(body)


def create_pure_python_pdf(filepath: str, title: str, lines: list[str]):
    """Creates a clean PDF using built-in Type1 Helvetica fonts (no Android PdfDocument errors)."""
    stream_parts = [
        "BT",
        "/F2 24 Tf",
        "0.83 0.18 0.18 rg",
        "50 765 Td",
        f"({_pdf_escape(title)}) Tj",
        "/F1 13 Tf",
        "0.15 0.18 0.25 rg",
        "0 -38 Td",
    ]
    for idx, line in enumerate(lines):
        if idx > 0:
            stream_parts.append("0 -24 Td")
        stream_parts.append(f"({_pdf_escape(line)}) Tj")
    stream_parts.append("ET")
    stream_data = "\n".join(stream_parts).encode("latin-1", errors="replace")

    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n",
        f"6 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("latin-1")
        + stream_data
        + b"\nendstream\nendobj\n",
    ]
    _write_pdf_objects(filepath, objs)


def create_vector_overlay_pdf(filepath: str, pw: float, ph: float, content_str: str):
    """Creates a transparent 1-page vector overlay PDF for highlights, ink, and text."""
    stream_data = content_str.encode("latin-1", errors="replace")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        f"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> "
        b"/ExtGState << /GS1 << /Type /ExtGState /ca 0.38 /CA 0.38 >> >> >> "
        b"/Contents 6 0 R >>\nendobj\n".encode("latin-1"),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n",
        f"6 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("latin-1")
        + stream_data
        + b"\nendstream\nendobj\n",
    ]
    _write_pdf_objects(filepath, objs)


def create_image_overlay_pdf(
    filepath: str,
    pw: float,
    ph: float,
    jpg_bytes: bytes,
    img_w: int,
    img_h: int,
    draw_x: float,
    draw_y: float,
    draw_w: float,
    draw_h: float,
):
    """Embeds a JPEG image XObject at exact PDF coordinates."""
    content_data = (
        f"q {draw_w:.2f} 0 0 {draw_h:.2f} {draw_x:.2f} {draw_y:.2f} cm /Im1 Do Q"
    ).encode("latin-1")

    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        f"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
        b"/Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n".encode("latin-1"),
        f"4 0 obj\n<< /Type /XObject /Subtype /Image /Width {img_w} /Height {img_h} "
        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpg_bytes)} >>\nstream\n".encode("latin-1")
        + jpg_bytes
        + b"\nendstream\nendobj\n",
        f"5 0 obj\n<< /Length {len(content_data)} >>\nstream\n".encode("latin-1")
        + content_data
        + b"\nendstream\nendobj\n",
    ]
    _write_pdf_objects(filepath, objs)


class StyledButton(Button):
    """Rounded flat button with density-independent sizing."""

    def __init__(self, bg_color=RED_PRIMARY, radius=dp(8), **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self._bg_color = bg_color
        self._radius = radius
        with self.canvas.before:
            self._col = Color(*bg_color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
        self.bind(pos=self._sync, size=self._sync)

    def set_bg(self, rgba):
        self._bg_color = rgba
        self._col.rgba = rgba

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


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
            self.current_line = Line(points=[touch.x, touch.y], width=dp(2.5))
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

        if mode == "sign" and self.app_ref.sig_jpg_bytes:
            self.app_ref.apply_signature_at(nx, ny)
            return True

        self.start_pt = (touch.x, touch.y)
        self.ink_pts = [(nx, ny)]

        if mode == "draw":
            with self.canvas.after:
                Color(0.86, 0.15, 0.15, 1)
                self.preview_line = Line(points=[touch.x, touch.y], width=dp(2.2))
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
        self.current_title = "Welcome.pdf"
        self.current_page = 0
        self.total_pages = 0
        self.zoom = 1.5
        self.auto_fit = True
        self.dark_mode = False
        self.tool_mode = "pan"
        self.sig_jpg_bytes = None
        self.sig_size = (200, 80)
        self.tts = None
        self._j = None
        self.recent_files = []

        if not LIBS_OK:
            return self._build_error_screen(f"Library Error:\n{LIBS_ERR}")

        try:
            self.sm = ScreenManager(transition=SlideTransition(duration=0.18))
            self.sm.add_widget(self._build_home_screen())
            self.sm.add_widget(self._build_reader_screen())
            Clock.schedule_once(self._post_startup, 0.3)
            return self.sm
        except Exception:
            return self._build_error_screen(traceback.format_exc())

    def _build_error_screen(self, err_text):
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        box.add_widget(Label(text="Startup Error Details:", size_hint_y=None, height=dp(32)))
        box.add_widget(TextInput(text=err_text, readonly=True))
        return box

    # -------------------- HOME SCREEN ("ON THIS DEVICE") --------------------

    def _build_home_screen(self):
        scr = Screen(name="home")
        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(*BG_LIGHT)
            self._home_bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, *_: setattr(self._home_bg, "pos", w.pos),
                  size=lambda w, *_: setattr(self._home_bg, "size", w.size))

        # Acrobat-style Red Top App Bar
        header = BoxLayout(size_hint_y=None, height=dp(56), padding=[dp(14), dp(8)], spacing=dp(8))
        with header.canvas.before:
            Color(*RED_PRIMARY)
            self._hdr_bg = Rectangle(pos=header.pos, size=header.size)
        header.bind(pos=lambda w, *_: setattr(self._hdr_bg, "pos", w.pos),
                    size=lambda w, *_: setattr(self._hdr_bg, "size", w.size))

        title_lbl = Label(
            text="AnTech Reader",
            bold=True,
            font_size=sp(19),
            color=(1, 1, 1, 1),
            halign="left",
            valign="middle",
        )
        title_lbl.bind(size=title_lbl.setter("text_size"))
        header.add_widget(title_lbl)

        open_top_btn = StyledButton(
            text="+ Open PDF",
            bg_color=RED_DARK,
            size_hint=(None, None),
            size=(dp(108), dp(38)),
            font_size=sp(14),
            bold=True,
            color=(1, 1, 1, 1),
        )
        open_top_btn.bind(on_release=lambda *_: self.pick_single_pdf())
        header.add_widget(open_top_btn)
        root.add_widget(header)

        # Quick Actions Card
        actions_wrap = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(116), padding=[dp(14), dp(12), dp(14), dp(4)], spacing=dp(8))
        main_open_btn = StyledButton(
            text="Browse PDFs on This Device",
            bg_color=RED_PRIMARY,
            size_hint_y=None,
            height=dp(46),
            font_size=sp(15),
            bold=True,
            color=(1, 1, 1, 1),
        )
        main_open_btn.bind(on_release=lambda *_: self.pick_single_pdf())
        actions_wrap.add_widget(main_open_btn)

        quick_row = GridLayout(cols=2, size_hint_y=None, height=dp(40), spacing=dp(8))
        merge_btn = StyledButton(
            text="Merge PDFs",
            bg_color=(0.20, 0.25, 0.33, 1),
            font_size=sp(13),
            bold=True,
            color=(1, 1, 1, 1),
        )
        merge_btn.bind(on_release=lambda *_: self.pick_merge_pdfs())

        img_btn = StyledButton(
            text="Images -> PDF",
            bg_color=(0.20, 0.25, 0.33, 1),
            font_size=sp(13),
            bold=True,
            color=(1, 1, 1, 1),
        )
        img_btn.bind(on_release=lambda *_: self.pick_images_to_pdf())

        quick_row.add_widget(merge_btn)
        quick_row.add_widget(img_btn)
        actions_wrap.add_widget(quick_row)
        root.add_widget(actions_wrap)

        # Section Title: "On this device"
        sec_lbl = Label(
            text="On this device",
            bold=True,
            font_size=sp(15),
            color=TEXT_DARK,
            size_hint_y=None,
            height=dp(34),
            padding=[dp(16), 0],
            halign="left",
            valign="middle",
        )
        sec_lbl.bind(size=sec_lbl.setter("text_size"))
        root.add_widget(sec_lbl)

        # Scrollable Recent Files List
        files_scroll = ScrollView()
        self.recent_list_box = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(8),
            padding=[dp(14), dp(4), dp(14), dp(14)],
        )
        self.recent_list_box.bind(minimum_height=self.recent_list_box.setter("height"))
        files_scroll.add_widget(self.recent_list_box)
        root.add_widget(files_scroll)

        # Bottom Navigation Bar
        nav_bar = GridLayout(cols=3, size_hint_y=None, height=dp(52), padding=[dp(8), dp(6)], spacing=dp(8))
        with nav_bar.canvas.before:
            Color(1, 1, 1, 1)
            self._nav_bg = Rectangle(pos=nav_bar.pos, size=nav_bar.size)
        nav_bar.bind(pos=lambda w, *_: setattr(self._nav_bg, "pos", w.pos),
                     size=lambda w, *_: setattr(self._nav_bg, "size", w.size))

        b_files = StyledButton(text="Files", bg_color=RED_PRIMARY, font_size=sp(13), bold=True, color=(1, 1, 1, 1))
        b_reader = StyledButton(text="Reader", bg_color=(0.91, 0.93, 0.96, 1), font_size=sp(13), bold=True, color=TEXT_DARK)
        b_reader.bind(on_release=lambda *_: self.go_to_reader())
        b_tools = StyledButton(text="Tools", bg_color=(0.91, 0.93, 0.96, 1), font_size=sp(13), bold=True, color=TEXT_DARK)
        b_tools.bind(on_release=lambda *_: self.open_tools_menu())

        nav_bar.add_widget(b_files)
        nav_bar.add_widget(b_reader)
        nav_bar.add_widget(b_tools)
        root.add_widget(nav_bar)

        scr.add_widget(root)
        return scr

    def _refresh_recent_list(self):
        self.recent_list_box.clear_widgets()
        for item in self.recent_files:
            card = BoxLayout(size_hint_y=None, height=dp(64), padding=[dp(10), dp(8)], spacing=dp(12))
            with card.canvas.before:
                Color(*CARD_WHITE)
                r = RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(10)])
            card.bind(pos=lambda w, *_, rect=r: setattr(rect, "pos", w.pos),
                      size=lambda w, *_, rect=r: setattr(rect, "size", w.size))

            badge = StyledButton(
                text="PDF",
                bg_color=RED_PRIMARY,
                radius=dp(8),
                size_hint=(None, None),
                size=(dp(44), dp(44)),
                font_size=sp(12),
                bold=True,
                color=(1, 1, 1, 1),
            )
            badge.bind(on_release=lambda *_, p=item["path"], t=item["title"]: self.open_local_pdf(p, t))
            card.add_widget(badge)

            info_btn = Button(
                text=f"[b]{item['title']}[/b]\n[color=#64748B]{item['subtitle']}[/color]",
                markup=True,
                background_normal="",
                background_color=(0, 0, 0, 0),
                color=TEXT_DARK,
                font_size=sp(14),
                halign="left",
                valign="middle",
            )
            info_btn.bind(size=info_btn.setter("text_size"))
            info_btn.bind(on_release=lambda *_, p=item["path"], t=item["title"]: self.open_local_pdf(p, t))
            card.add_widget(info_btn)

            self.recent_list_box.add_widget(card)

    def _record_recent(self, path: str, title: str, pages: int):
        size_kb = max(1, os.path.getsize(path) // 1024) if os.path.exists(path) else 1
        subtitle = f"{pages} page{'s' if pages != 1 else ''}  •  {size_kb} KB"
        self.recent_files = [r for r in self.recent_files if r["path"] != path]
        self.recent_files.insert(0, {"path": path, "title": title, "subtitle": subtitle})
        self.recent_files = self.recent_files[:15]
        try:
            meta_file = os.path.join(self.user_data_dir, "recent.json")
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(self.recent_files, f)
        except Exception:
            pass
        self._refresh_recent_list()

    # -------------------- READER SCREEN --------------------

    def _build_reader_screen(self):
        scr = Screen(name="reader")
        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(*CANVAS_BG)
            self._rdr_bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, *_: setattr(self._rdr_bg, "pos", w.pos),
                  size=lambda w, *_: setattr(self._rdr_bg, "size", w.size))

        # Red Reader Header Bar
        top_bar = BoxLayout(size_hint_y=None, height=dp(54), padding=[dp(8), dp(7)], spacing=dp(8))
        with top_bar.canvas.before:
            Color(*RED_PRIMARY)
            self._top_bg = Rectangle(pos=top_bar.pos, size=top_bar.size)
        top_bar.bind(pos=lambda w, *_: setattr(self._top_bg, "pos", w.pos),
                     size=lambda w, *_: setattr(self._top_bg, "size", w.size))

        back_btn = StyledButton(
            text="< Files",
            bg_color=RED_DARK,
            size_hint=(None, None),
            size=(dp(76), dp(38)),
            font_size=sp(13),
            bold=True,
            color=(1, 1, 1, 1),
        )
        back_btn.bind(on_release=lambda *_: self.go_to_home())
        top_bar.add_widget(back_btn)

        self.doc_title_lbl = Label(
            text="Welcome.pdf",
            bold=True,
            font_size=sp(15),
            color=(1, 1, 1, 1),
            shorten=True,
            shorten_from="right",
            halign="left",
            valign="middle",
        )
        self.doc_title_lbl.bind(size=self.doc_title_lbl.setter("text_size"))
        top_bar.add_widget(self.doc_title_lbl)

        find_btn = StyledButton(
            text="Find",
            bg_color=RED_DARK,
            size_hint=(None, None),
            size=(dp(62), dp(38)),
            font_size=sp(13),
            bold=True,
            color=(1, 1, 1, 1),
        )
        find_btn.bind(on_release=lambda *_: self.open_search_dialog())
        top_bar.add_widget(find_btn)

        save_btn = StyledButton(
            text="Save",
            bg_color=(1, 1, 1, 1),
            size_hint=(None, None),
            size=(dp(66), dp(38)),
            font_size=sp(13),
            bold=True,
            color=RED_PRIMARY,
        )
        save_btn.bind(on_release=lambda *_: self.save_pdf_dialog())
        top_bar.add_widget(save_btn)
        root.add_widget(top_bar)

        # Active Tool Banner (hidden when panning)
        self.tool_banner = BoxLayout(size_hint_y=None, height=0, opacity=0, padding=[dp(12), dp(4)], spacing=dp(8))
        with self.tool_banner.canvas.before:
            Color(0.12, 0.16, 0.23, 1)
            self._tb_bg = Rectangle(pos=self.tool_banner.pos, size=self.tool_banner.size)
        self.tool_banner.bind(pos=lambda w, *_: setattr(self._tb_bg, "pos", w.pos),
                              size=lambda w, *_: setattr(self._tb_bg, "size", w.size))

        self.tool_hint_lbl = Label(text="Tool: PAN", color=(1, 1, 1, 1), font_size=sp(13), halign="left", valign="middle")
        self.tool_hint_lbl.bind(size=self.tool_hint_lbl.setter("text_size"))
        self.tool_banner.add_widget(self.tool_hint_lbl)

        done_tool_btn = StyledButton(
            text="Done",
            bg_color=RED_PRIMARY,
            size_hint=(None, None),
            size=(dp(68), dp(30)),
            font_size=sp(12),
            bold=True,
            color=(1, 1, 1, 1),
        )
        done_tool_btn.bind(on_release=lambda *_: self.set_mode("pan"))
        self.tool_banner.add_widget(done_tool_btn)
        root.add_widget(self.tool_banner)

        # Centered Scrollable PDF Viewport
        self.viewer_scroll = ScrollView(do_scroll_x=True, do_scroll_y=True)
        self.canvas_holder = AnchorLayout(size_hint=(None, None), anchor_x="center", anchor_y="center")
        self.pdf_view = PDFPageCanvas(self, size_hint=(None, None))
        self.canvas_holder.add_widget(self.pdf_view)
        self.viewer_scroll.add_widget(self.canvas_holder)
        self.viewer_scroll.bind(size=lambda *_: self._sync_holder_size())
        root.add_widget(self.viewer_scroll)

        # Clean Status Strip
        self.status_lbl = Label(
            text="Ready",
            size_hint_y=None,
            height=dp(24),
            font_size=sp(12),
            color=TEXT_DARK,
        )
        root.add_widget(self.status_lbl)

        # Bottom Page & Tools Control Bar
        bottom_bar = BoxLayout(size_hint_y=None, height=dp(56), padding=[dp(8), dp(7)], spacing=dp(6))
        with bottom_bar.canvas.before:
            Color(1, 1, 1, 1)
            self._bot_bg = Rectangle(pos=bottom_bar.pos, size=bottom_bar.size)
        bottom_bar.bind(pos=lambda w, *_: setattr(self._bot_bg, "pos", w.pos),
                        size=lambda w, *_: setattr(self._bot_bg, "size", w.size))

        prev_btn = StyledButton(text="<", bg_color=(0.91, 0.93, 0.96, 1), size_hint_x=None, width=dp(44), font_size=sp(16), bold=True, color=TEXT_DARK)
        prev_btn.bind(on_release=lambda *_: self.change_page(-1))

        self.page_lbl = Label(text="1 / 1", size_hint_x=None, width=dp(64), font_size=sp(13), bold=True, color=TEXT_DARK)

        next_btn = StyledButton(text=">", bg_color=(0.91, 0.93, 0.96, 1), size_hint_x=None, width=dp(44), font_size=sp(16), bold=True, color=TEXT_DARK)
        next_btn.bind(on_release=lambda *_: self.change_page(1))

        z_out_btn = StyledButton(text="-", bg_color=(0.91, 0.93, 0.96, 1), size_hint_x=None, width=dp(42), font_size=sp(17), bold=True, color=TEXT_DARK)
        z_out_btn.bind(on_release=lambda *_: self.change_zoom(-0.25))

        z_in_btn = StyledButton(text="+", bg_color=(0.91, 0.93, 0.96, 1), size_hint_x=None, width=dp(42), font_size=sp(17), bold=True, color=TEXT_DARK)
        z_in_btn.bind(on_release=lambda *_: self.change_zoom(0.25))

        tools_btn = StyledButton(text="Tools", bg_color=RED_PRIMARY, font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        tools_btn.bind(on_release=lambda *_: self.open_tools_menu())

        bottom_bar.add_widget(prev_btn)
        bottom_bar.add_widget(self.page_lbl)
        bottom_bar.add_widget(next_btn)
        bottom_bar.add_widget(z_out_btn)
        bottom_bar.add_widget(z_in_btn)
        bottom_bar.add_widget(tools_btn)
        root.add_widget(bottom_bar)

        scr.add_widget(root)
        return scr

    def go_to_home(self):
        self.set_mode("pan")
        self.sm.transition.direction = "right"
        self.sm.current = "home"

    def go_to_reader(self):
        self.sm.transition.direction = "left"
        self.sm.current = "reader"
        Clock.schedule_once(lambda *_: self.render_current_page(), 0.05)

    def _sync_holder_size(self):
        pad = dp(16)
        hw = max(self.viewer_scroll.width, self.pdf_view.width + pad)
        hh = max(self.viewer_scroll.height, self.pdf_view.height + pad)
        self.canvas_holder.size = (hw, hh)

    # -------------------- STARTUP & WELCOME PDF --------------------

    def _java(self):
        if self._j is not None:
            return self._j
        from jnius import autoclass
        self._j = {
            "PythonActivity": autoclass("org.kivy.android.PythonActivity"),
            "Intent": autoclass("android.content.Intent"),
            "File": autoclass("java.io.File"),
            "ParcelFileDescriptor": autoclass("android.os.ParcelFileDescriptor"),
            "PdfRenderer": autoclass("android.graphics.pdf.PdfRenderer"),
            "Bitmap": autoclass("android.graphics.Bitmap"),
            "BitmapConfig": autoclass("android.graphics.Bitmap$Config"),
            "Canvas": autoclass("android.graphics.Canvas"),
            "AndroidColor": autoclass("android.graphics.Color"),
            "ByteArrayOutputStream": autoclass("java.io.ByteArrayOutputStream"),
            "CompressFormat": autoclass("android.graphics.Bitmap$CompressFormat"),
            "TextToSpeech": autoclass("android.speech.tts.TextToSpeech"),
            "Locale": autoclass("java.util.Locale"),
        }
        return self._j

    def _post_startup(self, *_):
        if platform == "android":
            try:
                from android import activity
                activity.bind(on_activity_result=self.on_activity_result)
            except Exception:
                pass

        os.makedirs(self.user_data_dir, exist_ok=True)
        meta_file = os.path.join(self.user_data_dir, "recent.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    self.recent_files = json.load(f)
            except Exception:
                self.recent_files = []

        sample_path = os.path.join(self.user_data_dir, "Welcome_to_AnTech_Reader.pdf")
        create_pure_python_pdf(
            sample_path,
            "AnTech Reader",
            [
                "Welcome to AnTech Reader for Android!",
                "1. Tap 'Open PDF' or select any document from your device.",
                "2. Tap 'Tools' at the bottom to Highlight, Add Text, or Draw.",
                "3. Use 'Fill & Sign' to draw and stamp your signature anywhere.",
                "4. Merge PDFs, Split pages, Convert Images to PDF, and Protect.",
            ],
        )
        self.open_local_pdf(sample_path, "Welcome_to_AnTech_Reader.pdf", switch_screen=False)

    # -------------------- RENDERING --------------------

    def open_local_pdf(self, path: str, title: str | None = None, switch_screen: bool = True):
        self.current_page = 0
        self.auto_fit = True
        self.current_title = title or os.path.basename(path)
        self.doc_title_lbl.text = self.current_title
        self.load_pdf(path, record=True)
        if switch_screen:
            self.go_to_reader()

    def load_pdf(self, path: str, record: bool = False):
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                self.prompt_password_and_decrypt(path)
                return
            self.current_pdf = path
            self.total_pages = len(reader.pages)
            self.current_page = max(0, min(self.current_page, self.total_pages - 1))
            if record:
                self._record_recent(path, self.current_title, self.total_pages)
            self.render_current_page()
        except Exception as e:
            self.status_lbl.text = f"Error: {e}"

    def render_current_page(self):
        if not self.current_pdf:
            return
        self.page_lbl.text = f"{self.current_page + 1} / {self.total_pages}"

        if platform == "android":
            try:
                j = self._java()
                java_file = j["File"](self.current_pdf)
                pfd = j["ParcelFileDescriptor"].open(java_file, j["ParcelFileDescriptor"].MODE_READ_ONLY)
                renderer = j["PdfRenderer"](pfd)
                page = renderer.openPage(self.current_page)

                raw_w = max(1, page.getWidth())
                raw_h = max(1, page.getHeight())
                if self.auto_fit and self.viewer_scroll.width > dp(40):
                    self.zoom = max(0.8, min(3.2, (self.viewer_scroll.width - dp(24)) / float(raw_w)))
                    self.auto_fit = False

                w = max(200, int(raw_w * self.zoom))
                h = max(200, int(raw_h * self.zoom))
                bitmap = j["Bitmap"].createBitmap(w, h, j["BitmapConfig"].ARGB_8888)
                canvas = j["Canvas"](bitmap)
                canvas.drawColor(j["AndroidColor"].WHITE)
                page.render(bitmap, None, None, 1)
                page.close()
                renderer.close()
                pfd.close()

                baos = j["ByteArrayOutputStream"]()
                bitmap.compress(j["CompressFormat"].PNG, 100, baos)
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
                self._sync_holder_size()
            except Exception as e:
                self.status_lbl.text = f"Render note: {e}"
        else:
            self.pdf_view.size = (dp(340), dp(480))
            self._sync_holder_size()

    def change_page(self, delta: int):
        if not self.current_pdf:
            return
        new_p = self.current_page + delta
        if 0 <= new_p < self.total_pages:
            self.current_page = new_p
            self.render_current_page()

    def change_zoom(self, delta: float):
        self.auto_fit = False
        self.zoom = max(0.6, min(3.5, self.zoom + delta))
        self.render_current_page()

    def toggle_dark_mode(self):
        self.dark_mode = not self.dark_mode
        self.render_current_page()
        self.status_lbl.text = "Dark Mode ON" if self.dark_mode else "Dark Mode OFF"

    def set_mode(self, mode: str):
        self.tool_mode = mode
        if mode == "pan":
            self.tool_banner.height = 0
            self.tool_banner.opacity = 0
            self.status_lbl.text = "Ready"
        else:
            hints = {
                "highlight": "Highlight: Drag across text on page",
                "text": "Add Text: Tap where you want to type",
                "draw": "Draw: Drag finger on page to draw",
                "sign": "Signature: Tap on page to stamp signature",
            }
            self.tool_hint_lbl.text = hints.get(mode, f"Tool: {mode.upper()}")
            self.tool_banner.height = dp(40)
            self.tool_banner.opacity = 1
            self.status_lbl.text = f"Active: {mode.upper()}"

    # -------------------- TOOLS MODAL --------------------

    def open_tools_menu(self):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        grid = GridLayout(cols=2, spacing=dp(8))

        items = [
            ("Highlight", lambda *_: self._pick_tool("highlight")),
            ("Add Text", lambda *_: self._pick_tool("text")),
            ("Freehand Draw", lambda *_: self._pick_tool("draw")),
            ("Fill & Sign", lambda *_: self._run_from_menu(self.open_signature_dialog)),
            ("Dark Mode", lambda *_: self._run_from_menu(self.toggle_dark_mode)),
            ("Rotate 90°", lambda *_: self._run_from_menu(self.rotate_page)),
            ("Delete Page", lambda *_: self._run_from_menu(self.delete_page)),
            ("Merge PDFs", lambda *_: self._run_from_menu(self.pick_merge_pdfs)),
            ("Split / Extract", lambda *_: self._run_from_menu(self.open_split_dialog)),
            ("Images -> PDF", lambda *_: self._run_from_menu(self.pick_images_to_pdf)),
            ("Password Protect", lambda *_: self._run_from_menu(self.open_encrypt_dialog)),
            ("Read Aloud", lambda *_: self._run_from_menu(self.read_aloud)),
        ]
        for label, cb in items:
            b = StyledButton(text=label, bg_color=(0.20, 0.25, 0.33, 1), font_size=sp(13), bold=True, color=(1, 1, 1, 1))
            b.bind(on_release=cb)
            grid.add_widget(b)

        box.add_widget(grid)
        close_btn = StyledButton(text="Close", bg_color=RED_PRIMARY, size_hint_y=None, height=dp(42), font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        box.add_widget(close_btn)

        self._tools_popup = Popup(title="AnTech Reader Tools", content=box, size_hint=(0.92, 0.72))
        close_btn.bind(on_release=self._tools_popup.dismiss)
        self._tools_popup.open()

    def _pick_tool(self, mode: str):
        if hasattr(self, "_tools_popup"):
            self._tools_popup.dismiss()
        if self.sm.current != "reader":
            self.go_to_reader()
        self.set_mode(mode)

    def _run_from_menu(self, fn):
        if hasattr(self, "_tools_popup"):
            self._tools_popup.dismiss()
        fn()

    # -------------------- PURE-PYTHON PDF ANNOTATIONS & SIGNING --------------------

    def _apply_overlay_file(self, overlay_pdf: str):
        reader = PdfReader(self.current_pdf)
        overlay_reader = PdfReader(overlay_pdf)
        writer = PdfWriter()
        for idx, p in enumerate(reader.pages):
            if idx == self.current_page:
                p.merge_page(overlay_reader.pages[0])
            writer.add_page(p)

        updated_path = os.path.join(self.user_data_dir, f"edit_{int(time.time() * 1000)}.pdf")
        with open(updated_path, "wb") as f:
            writer.write(f)
        self.load_pdf(updated_path, record=False)

    def apply_highlight_box(self, nx1: float, ny1: float, nx2: float, ny2: float):
        if not self.current_pdf:
            return
        try:
            reader = PdfReader(self.current_pdf)
            page = reader.pages[self.current_page]
            pw, ph = float(page.mediabox.width), float(page.mediabox.height)
            x0, x1 = sorted([nx1 * pw, nx2 * pw])
            y0, y1 = sorted([(1.0 - ny1) * ph, (1.0 - ny2) * ph])
            w, h = x1 - x0, y1 - y0
            if w <= 3 or h <= 3:
                return
            content = f"q /GS1 gs 1 0.92 0.23 rg {x0:.2f} {y0:.2f} {w:.2f} {h:.2f} re f Q"
            ov = os.path.join(self.user_data_dir, "overlay.pdf")
            create_vector_overlay_pdf(ov, pw, ph, content)
            self._apply_overlay_file(ov)
            self.status_lbl.text = "Highlight added"
        except Exception as e:
            self.status_lbl.text = f"Highlight error: {e}"

    def apply_ink_strokes(self, norm_pts: list[tuple[float, float]]):
        if not self.current_pdf or len(norm_pts) < 2:
            return
        try:
            reader = PdfReader(self.current_pdf)
            page = reader.pages[self.current_page]
            pw, ph = float(page.mediabox.width), float(page.mediabox.height)
            cmds = ["q 0.86 0.15 0.15 RG 2.2 w 1 J 1 j"]
            for i, (nx, ny) in enumerate(norm_pts):
                px, py = nx * pw, (1.0 - ny) * ph
                cmds.append(f"{px:.2f} {py:.2f} {'m' if i == 0 else 'l'}")
            cmds.append("S Q")
            ov = os.path.join(self.user_data_dir, "overlay.pdf")
            create_vector_overlay_pdf(ov, pw, ph, "\n".join(cmds))
            self._apply_overlay_file(ov)
            self.status_lbl.text = "Drawing added"
        except Exception as e:
            self.status_lbl.text = f"Draw error: {e}"

    def prompt_add_text(self, nx: float, ny: float):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        inp = TextInput(hint_text="Type text to insert...", multiline=False, font_size=sp(15))
        btn = StyledButton(text="Insert Text", bg_color=RED_PRIMARY, size_hint_y=None, height=dp(44), font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        box.add_widget(inp)
        box.add_widget(btn)
        pop = Popup(title="Add Text", content=box, size_hint=(0.88, 0.32))

        def _insert(*_):
            txt = inp.text.strip()
            pop.dismiss()
            if txt and self.current_pdf:
                try:
                    reader = PdfReader(self.current_pdf)
                    page = reader.pages[self.current_page]
                    pw, ph = float(page.mediabox.width), float(page.mediabox.height)
                    px, py = nx * pw, (1.0 - ny) * ph
                    content = f"BT /F2 15 Tf 0.06 0.18 0.72 rg {px:.2f} {py:.2f} Td ({_pdf_escape(txt)}) Tj ET"
                    ov = os.path.join(self.user_data_dir, "overlay.pdf")
                    create_vector_overlay_pdf(ov, pw, ph, content)
                    self._apply_overlay_file(ov)
                    self.status_lbl.text = "Text inserted"
                except Exception as e:
                    self.status_lbl.text = f"Text error: {e}"

        btn.bind(on_release=_insert)
        pop.open()

    def open_signature_dialog(self):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        pad = SignaturePad(size_hint_y=1)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        btn_clear = StyledButton(text="Clear", bg_color=(0.35, 0.40, 0.48, 1), font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        btn_use = StyledButton(text="Use Signature", bg_color=RED_PRIMARY, font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        row.add_widget(btn_clear)
        row.add_widget(btn_use)
        box.add_widget(pad)
        box.add_widget(row)

        pop = Popup(title="Draw Your Signature", content=box, size_hint=(0.92, 0.52))
        btn_clear.bind(on_release=lambda *_: pad.clear_pad())

        def _save_sig(*_):
            sig_png = os.path.join(self.user_data_dir, "signature.png")
            pad.export_to_png(sig_png)
            im = PILImage.open(sig_png).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=90)
            self.sig_jpg_bytes = buf.getvalue()
            self.sig_size = im.size
            pop.dismiss()
            if self.sm.current != "reader":
                self.go_to_reader()
            self.set_mode("sign")

        btn_use.bind(on_release=_save_sig)
        pop.open()

    def apply_signature_at(self, nx: float, ny: float):
        if not self.current_pdf or not self.sig_jpg_bytes:
            return
        try:
            reader = PdfReader(self.current_pdf)
            page = reader.pages[self.current_page]
            pw, ph = float(page.mediabox.width), float(page.mediabox.height)
            draw_w, draw_h = 135.0, 56.0
            draw_x = max(0.0, min(pw - draw_w, nx * pw))
            draw_y = max(0.0, min(ph - draw_h, (1.0 - ny) * ph - draw_h))
            ov = os.path.join(self.user_data_dir, "sig_overlay.pdf")
            create_image_overlay_pdf(
                ov, pw, ph, self.sig_jpg_bytes, self.sig_size[0], self.sig_size[1], draw_x, draw_y, draw_w, draw_h
            )
            self._apply_overlay_file(ov)
            self.set_mode("pan")
            self.status_lbl.text = "Signature stamped"
        except Exception as e:
            self.status_lbl.text = f"Signature error: {e}"

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
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        from_in = TextInput(text="1", hint_text="From page", input_filter="int", multiline=False)
        to_in = TextInput(text=str(self.total_pages), hint_text="To page", input_filter="int", multiline=False)
        btn = StyledButton(text="Extract Range", bg_color=RED_PRIMARY, size_hint_y=None, height=dp(44), font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        box.add_widget(from_in)
        box.add_widget(to_in)
        box.add_widget(btn)
        pop = Popup(title="Split / Extract Pages", content=box, size_hint=(0.88, 0.42))

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
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        pw_in = TextInput(hint_text="Set password...", password=True, multiline=False)
        btn = StyledButton(text="Encrypt & Save PDF", bg_color=RED_PRIMARY, size_hint_y=None, height=dp(44), font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        box.add_widget(pw_in)
        box.add_widget(btn)
        pop = Popup(title="Password Protect PDF", content=box, size_hint=(0.88, 0.35))

        def _lock(*_):
            pw = pw_in.text.strip()
            pop.dismiss()
            if pw:
                reader = PdfReader(self.current_pdf)
                writer = PdfWriter()
                for p in reader.pages:
                    writer.add_page(p)
                writer.encrypt(pw)
                out = os.path.join(self.user_data_dir, "protected.pdf")
                with open(out, "wb") as f:
                    writer.write(f)
                self.current_pdf = out
                self.save_pdf_dialog("AnTech_Reader_Protected.pdf")

        btn.bind(on_release=_lock)
        pop.open()

    def prompt_password_and_decrypt(self, path: str):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        pw_in = TextInput(hint_text="Enter PDF password...", password=True, multiline=False)
        btn = StyledButton(text="Unlock", bg_color=RED_PRIMARY, size_hint_y=None, height=dp(44), font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        box.add_widget(pw_in)
        box.add_widget(btn)
        pop = Popup(title="Encrypted PDF", content=box, size_hint=(0.88, 0.35))

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
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        q_in = TextInput(hint_text="Search word...", multiline=False)
        btn = StyledButton(text="Find", bg_color=RED_PRIMARY, size_hint_y=None, height=dp(44), font_size=sp(14), bold=True, color=(1, 1, 1, 1))
        box.add_widget(q_in)
        box.add_widget(btn)
        pop = Popup(title="Search PDF", content=box, size_hint=(0.88, 0.35))

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
        if platform == "android":
            try:
                j = self._java()
                if self.tts is None:
                    self.tts = j["TextToSpeech"](j["PythonActivity"].mActivity, None)
                self.tts.setLanguage(j["Locale"].US)
                self.tts.speak(text[:3500], 0, None, "antech_reader_tts")
                self.status_lbl.text = "Reading page aloud..."
            except Exception as e:
                self.status_lbl.text = f"TTS note: {e}"

    # -------------------- ANDROID FILE PICKER & SAVING --------------------

    def _launch_picker(self, mime_type: str, req_code: int, multiple: bool = False):
        if platform != "android":
            return
        j = self._java()
        intent = j["Intent"](j["Intent"].ACTION_OPEN_DOCUMENT)
        intent.addCategory(j["Intent"].CATEGORY_OPENABLE)
        intent.setType(mime_type)
        if multiple:
            intent.putExtra(j["Intent"].EXTRA_ALLOW_MULTIPLE, True)
        j["PythonActivity"].mActivity.startActivityForResult(intent, req_code)

    def pick_single_pdf(self):
        self._launch_picker("application/pdf", REQ_OPEN_PDF, multiple=False)

    def pick_merge_pdfs(self):
        self._launch_picker("application/pdf", REQ_MERGE_PDFS, multiple=True)

    def pick_images_to_pdf(self):
        self._launch_picker("image/*", REQ_IMAGES_TO_PDF, multiple=True)

    def save_pdf_dialog(self, suggested_name: str | None = None):
        if not self.current_pdf or platform != "android":
            return
        j = self._java()
        name = suggested_name or f"AnTech_Reader_{int(time.time())}.pdf"
        intent = j["Intent"](j["Intent"].ACTION_CREATE_DOCUMENT)
        intent.addCategory(j["Intent"].CATEGORY_OPENABLE)
        intent.setType("application/pdf")
        intent.putExtra(j["Intent"].EXTRA_TITLE, name)
        j["PythonActivity"].mActivity.startActivityForResult(intent, REQ_SAVE_PDF)

    def _copy_uri_to_local(self, uri, filename: str) -> str:
        j = self._java()
        resolver = j["PythonActivity"].mActivity.getContentResolver()
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
            local_pdf = self._copy_uri_to_local(uris[0], f"doc_{int(time.time())}.pdf")
            display_name = os.path.basename(str(uris[0].getLastPathSegment() or "Document.pdf"))
            if not display_name.lower().endswith(".pdf"):
                display_name += ".pdf"
            Clock.schedule_once(lambda *_: self.open_local_pdf(local_pdf, display_name, switch_screen=True), 0)

        elif request_code == REQ_MERGE_PDFS:
            writer = PdfWriter()
            for idx, u in enumerate(uris):
                part = self._copy_uri_to_local(u, f"merge_{idx}.pdf")
                reader = PdfReader(part)
                for p in reader.pages:
                    writer.add_page(p)
            merged_out = os.path.join(self.user_data_dir, f"Merged_{int(time.time())}.pdf")
            with open(merged_out, "wb") as f:
                writer.write(f)
            Clock.schedule_once(lambda *_: self.open_local_pdf(merged_out, os.path.basename(merged_out), switch_screen=True), 0)

        elif request_code == REQ_IMAGES_TO_PDF:
            writer = PdfWriter()
            for idx, u in enumerate(uris):
                img_path = self._copy_uri_to_local(u, f"img_{idx}.png")
                im = PILImage.open(img_path).convert("RGB")
                iw, ih = im.size
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=88)
                single_pdf = os.path.join(self.user_data_dir, f"img_page_{idx}.pdf")
                create_image_overlay_pdf(single_pdf, float(iw), float(ih), buf.getvalue(), iw, ih, 0.0, 0.0, float(iw), float(ih))
                page_reader = PdfReader(single_pdf)
                writer.add_page(page_reader.pages[0])
            out_pdf = os.path.join(self.user_data_dir, f"Images_{int(time.time())}.pdf")
            with open(out_pdf, "wb") as f:
                writer.write(f)
            Clock.schedule_once(lambda *_: self.open_local_pdf(out_pdf, os.path.basename(out_pdf), switch_screen=True), 0)

        elif request_code == REQ_SAVE_PDF:
            try:
                j = self._java()
                resolver = j["PythonActivity"].mActivity.getContentResolver()
                out_stream = resolver.openOutputStream(uris[0])
                with open(self.current_pdf, "rb") as src:
                    out_stream.write(src.read())
                out_stream.flush()
                out_stream.close()
                Clock.schedule_once(lambda *_: setattr(self.status_lbl, "text", "PDF saved successfully!"), 0)
            except Exception as e:
                Clock.schedule_once(lambda *_: setattr(self.status_lbl, "text", f"Save error: {e}"), 0)


if __name__ == "__main__":
    AnTechReaderApp().run()
