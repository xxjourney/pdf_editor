"""PyQt6 native PDF editor with PyMuPDF rendering."""
import io
import math
import os
import sys

import fitz  # PyMuPDF
from PyQt6.QtCore import (
    QPoint, QPointF, QRectF, Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QFontMetrics, QFontMetricsF, QImage, QPainter,
    QPen, QPixmap, QTransform,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QProgressDialog, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QStatusBar, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

USE_REMBG = False   # set False to skip rembg even if installed

HIGHLIGHT_COLOR = QColor(255, 220, 0, 110)
SELECTION_COLOR = QColor(100, 149, 237, 80)
WM_HANDLE_R    = 6
WM_PADDING     = 4
WM_ROT_OFFSET  = 24


# ---------------------------------------------------------------------------
# Watermark helpers
# ---------------------------------------------------------------------------

def _default_watermark() -> dict:
    return {
        "type":          "text",   # "text" | "image"
        "text":          "",
        "image_path":    "",
        "image_pixmap":  None,     # QPixmap for display
        "image_bytes":   None,     # PNG bytes for export (full opacity; faded at paint/export time)
        "x_pct":         0.5,
        "y_pct":         0.5,
        "fontsize":      48,       # pt (text) or display height in pt (image)
        "angle":         45,
        "opacity":       0.35,
        "visible":       True,
    }


def _process_watermark_image(path: str,
                              opacity: float = 1.0) -> tuple:
    """Load image, remove background, fade.
    Returns (QPixmap, png_bytes, error_str).  error_str is None on success."""
    try:
        from PIL import Image as _PIL
    except ImportError:
        return None, None, "❌ 請安裝 Pillow: pip install Pillow"
    try:
        img = _PIL.open(path).convert("RGBA")

        # --- background removal ---
        if USE_REMBG:
            try:
                from rembg import remove as _rembg
                img = _rembg(img)
            except ImportError:
                img = _remove_light_bg(img)
        else:
            img = _remove_light_bg(img)

        # --- fade (scale alpha channel) ---
        import numpy as _np
        arr = _np.array(img, dtype=_np.float32)
        arr[:, :, 3] = arr[:, :, 3] * opacity
        import numpy as np
        img = _PIL.fromarray(arr.astype(np.uint8))

        # --- to QPixmap ---
        data = img.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height,
                      img.width * 4, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qimg.copy())

        # --- PNG bytes for export ---
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        return pixmap, img_bytes, None
    except Exception as exc:
        return None, None, f"❌ 圖片處理失敗: {exc}"


def _remove_light_bg(img):
    """Threshold-based white background removal (Pillow fallback)."""
    try:
        import numpy as _np
        arr = _np.array(img.convert("RGBA"))
        lightness = arr[:, :, :3].mean(axis=2)
        arr[:, :, 3] = _np.where(lightness > 200, 0, arr[:, :, 3])
        from PIL import Image as _PIL
        return _PIL.fromarray(arr)
    except ImportError:
        from PIL import Image as _PIL
        img = img.convert("RGBA")
        data = list(img.getdata())
        img.putdata([
            (r, g, b, 0) if (r + g + b) / 3 > 200 else (r, g, b, a)
            for r, g, b, a in data
        ])
        return img


def _rotate_image_bytes(img_bytes: bytes, angle: int) -> bytes:
    """Rotate image (CCW-positive degrees) and return PNG bytes."""
    if angle == 0:
        return img_bytes
    try:
        from PIL import Image as _PIL
        img = _PIL.open(io.BytesIO(img_bytes))
        rotated = img.rotate(angle, expand=True,
                             resample=_PIL.Resampling.BICUBIC)
        buf = io.BytesIO()
        rotated.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return img_bytes


# ---------------------------------------------------------------------------
# Page view widget
# ---------------------------------------------------------------------------

class PageView(QWidget):
    selectionReady = pyqtSignal(str, list, QPoint)
    zoomRequested  = pyqtSignal(int)
    pageRequested  = pyqtSignal(int)   # +1 = next page, -1 = prev page
    wmChanged      = pyqtSignal(int, float, float, int, int)  # idx,x,y,fs,ang
    wmClicked      = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._page:   fitz.Page | None = None
        self._zoom:   float = 1.0
        self._highlights: list[dict] = []
        self._page_index: int = 0

        self._words:      list = []
        self._sel_start:  int  = -1
        self._sel_end:    int  = -1
        self._sel_active: bool = False

        self._watermarks:    list[dict] = []
        self._wm_active_idx: int = -1
        self._wm_drag_idx:   int = -1

        self._wm_drag_mode:            str | None   = None
        self._wm_drag_origin:          QPointF | None = None
        self._wm_drag_start_x_pct:     float = 0.5
        self._wm_drag_start_y_pct:     float = 0.5
        self._wm_drag_start_fontsize:  int   = 48
        self._wm_drag_start_angle:     int   = 45
        self._wm_drag_start_scrangle:  float = 0.0
        self._wm_drag_start_resize_lx: float = 1.0

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_page(self, page: fitz.Page, page_index: int, zoom: float,
                 highlights: list[dict]) -> None:
        self._page       = page
        self._page_index = page_index
        self._zoom       = zoom
        self._highlights = highlights
        self._words      = page.get_text("words")
        self._sel_start  = self._sel_end = -1
        self._sel_active = False
        self._render()

    def update_highlights(self, highlights: list[dict]) -> None:
        self._highlights = highlights
        self.update()

    def set_watermarks(self, watermarks: list[dict],
                       active_idx: int = -1) -> None:
        self._watermarks    = list(watermarks)
        self._wm_active_idx = active_idx
        self.update()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> None:
        if self._page is None:
            return
        dpr = self.devicePixelRatioF()
        mat = fitz.Matrix(self._zoom * dpr, self._zoom * dpr)
        pix = self._page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(bytes(pix.samples), pix.width, pix.height,
                     pix.stride, QImage.Format.Format_RGB888)
        pm = QPixmap.fromImage(img.copy())
        pm.setDevicePixelRatio(dpr)
        self._pixmap = pm
        self.setFixedSize(round(pix.width / dpr), round(pix.height / dpr))
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)

        # 1. Page content
        if self._pixmap:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawPixmap(0, 0, self._pixmap)

        # 2. Watermarks — Multiply keeps dark text intact, tints white areas
        if self._watermarks:
            self._paint_all_watermarks(painter)

        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)

        # 3. Highlights
        for hl in self._highlights:
            if hl["page"] == self._page_index:
                painter.fillRect(self._pdf_to_screen(hl["rect"]),
                                 HIGHLIGHT_COLOR)

        # 4. Text selection
        if self._sel_start >= 0 and self._sel_end >= 0:
            lo, hi = sorted((self._sel_start, self._sel_end))
            for i in range(lo, hi + 1):
                painter.fillRect(
                    self._pdf_to_screen(fitz.Rect(self._words[i][:4])),
                    SELECTION_COLOR)

        # 5. Bounding-box / handles overlay (always on top)
        if self._watermarks:
            self._paint_wm_handles(painter)

        painter.end()

    def _paint_all_watermarks(self, painter: QPainter) -> None:
        """Paint watermark content (text or image) — drawn before page pixmap."""
        for idx, wm in enumerate(self._watermarks):
            if not wm.get("visible", True):
                continue
            if wm["type"] == "image":
                pm = wm.get("image_pixmap")
                if pm is None:
                    continue
                self._paint_image_wm(painter, wm)
            else:
                if not wm["text"].strip():
                    continue
                self._paint_text_wm(painter, wm)

    def _paint_text_wm(self, painter: QPainter, wm: dict) -> None:
        text_w, text_h = self._wm_size_for(wm)
        ax = wm["x_pct"] * self.width()
        ay = wm["y_pct"] * self.height()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(ax, ay)
        painter.rotate(-wm["angle"])

        # Multiply: text "tints" light areas but never covers dark ink
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Multiply)
        painter.setOpacity(wm.get("opacity", 0.35))
        painter.setFont(self._wm_qfont_for(wm))
        painter.setPen(QColor(160, 160, 160))
        painter.drawText(
            QRectF(-text_w / 2, -text_h / 2, text_w, text_h),
            Qt.AlignmentFlag.AlignCenter,
            wm["text"],
        )
        painter.restore()

    def _paint_image_wm(self, painter: QPainter, wm: dict) -> None:
        pm = wm["image_pixmap"]
        text_w, text_h = self._wm_size_for(wm)
        ax = wm["x_pct"] * self.width()
        ay = wm["y_pct"] * self.height()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(ax, ay)
        painter.rotate(-wm["angle"])
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setOpacity(wm.get("opacity", 0.35))
        painter.drawPixmap(
            round(-text_w / 2), round(-text_h / 2),
            round(text_w), round(text_h),
            pm)
        painter.restore()

    def _paint_wm_handles(self, painter: QPainter) -> None:
        """Paint bounding box + drag handles for the active watermark only."""
        idx = self._wm_active_idx
        if idx < 0 or idx >= len(self._watermarks):
            return
        wm = self._watermarks[idx]
        if not wm.get("visible", True):
            return
        if wm["type"] == "image" and wm.get("image_pixmap") is None:
            return
        if wm["type"] == "text" and not wm["text"].strip():
            return

        text_w, text_h = self._wm_size_for(wm)
        p = WM_PADDING
        ax = wm["x_pct"] * self.width()
        ay = wm["y_pct"] * self.height()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.translate(ax, ay)
        painter.rotate(-wm["angle"])

        box = QRectF(-text_w / 2 - p, -text_h / 2 - p,
                     text_w + p * 2, text_h + p * 2)
        painter.setPen(QPen(QColor(100, 149, 237, 180), 1,
                            Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box)

        rh = QPointF(box.right(), 0.0)
        painter.setPen(QPen(QColor(100, 149, 237)))
        painter.setBrush(QColor(100, 149, 237))
        painter.drawEllipse(rh, WM_HANDLE_R, WM_HANDLE_R)

        rot_y = box.top() - WM_ROT_OFFSET
        rot_h = QPointF(0.0, rot_y)
        painter.setPen(QPen(QColor(255, 140, 0)))
        painter.drawLine(QPointF(0.0, box.top()), rot_h)
        painter.setBrush(QColor(255, 140, 0))
        painter.drawEllipse(rot_h, WM_HANDLE_R, WM_HANDLE_R)

        painter.restore()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()

        if self._watermarks:
            wm_idx, hit = self._wm_hit_test(pos)
            if hit is not None:
                wm = self._watermarks[wm_idx]
                self._wm_drag_idx           = wm_idx
                self._wm_drag_mode          = hit
                self._wm_drag_origin        = event.position()
                self._wm_drag_start_x_pct   = wm["x_pct"]
                self._wm_drag_start_y_pct   = wm["y_pct"]
                self._wm_drag_start_fontsize = wm["fontsize"]
                self._wm_drag_start_angle   = wm["angle"]
                ax = wm["x_pct"] * self.width()
                ay = wm["y_pct"] * self.height()
                self._wm_drag_start_scrangle = math.degrees(
                    math.atan2(event.position().y() - ay,
                               event.position().x() - ax))
                if hit == 'resize':
                    inv, _ = self._wm_transform_for(wm).inverted()
                    self._wm_drag_start_resize_lx = max(
                        1.0, abs(inv.map(event.position()).x()))
                if wm_idx != self._wm_active_idx:
                    self._wm_active_idx = wm_idx
                    self.wmClicked.emit(wm_idx)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                return

        idx = self._word_at(pos)
        self._sel_start = self._sel_end = idx
        self._sel_active = True
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._wm_drag_mode:
            self._handle_wm_drag(event.position())
            return
        if self._sel_active:
            idx = self._word_at(event.position().toPoint())
            if idx != self._sel_end:
                self._sel_end = idx
                self.update()
            return
        if self._watermarks:
            _, hit = self._wm_hit_test(event.position().toPoint())
            cursors = {
                'move':   Qt.CursorShape.SizeAllCursor,
                'resize': Qt.CursorShape.SizeHorCursor,
                'rotate': Qt.CursorShape.PointingHandCursor,
            }
            self.setCursor(cursors.get(hit, Qt.CursorShape.IBeamCursor))
        else:
            self.setCursor(Qt.CursorShape.IBeamCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._wm_drag_mode:
            self._wm_drag_mode = None
            self._wm_drag_idx  = -1
            self.setCursor(Qt.CursorShape.IBeamCursor)
            return
        if self._sel_active:
            self._sel_active = False
            lo, hi = sorted((self._sel_start, self._sel_end))
            if lo >= 0 and hi >= 0:
                sel  = self._words[lo:hi + 1]
                text = " ".join(w[4] for w in sel)
                rects = [fitz.Rect(w[:4]) for w in sel]
                if text.strip():
                    self.selectionReady.emit(
                        text, rects,
                        self.mapToGlobal(event.position().toPoint()))
            self.update()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoomRequested.emit(1)
            elif delta < 0:
                self.zoomRequested.emit(-1)
            event.accept()
        else:
            delta = event.angleDelta().y()
            if delta != 0:
                self.pageRequested.emit(-1 if delta > 0 else 1)
            event.ignore()  # let QScrollArea handle in-page scrolling

    # ------------------------------------------------------------------
    # Watermark drag
    # ------------------------------------------------------------------

    def _handle_wm_drag(self, pos: QPointF) -> None:
        idx = self._wm_drag_idx
        if idx < 0 or idx >= len(self._watermarks):
            return
        wm = self._watermarks[idx]

        if self._wm_drag_mode == 'move':
            dx = (pos.x() - self._wm_drag_origin.x()) / self.width()
            dy = (pos.y() - self._wm_drag_origin.y()) / self.height()
            wm["x_pct"] = max(0.0, min(1.0, self._wm_drag_start_x_pct + dx))
            wm["y_pct"] = max(0.0, min(1.0, self._wm_drag_start_y_pct + dy))
            self.wmChanged.emit(idx, wm["x_pct"], wm["y_pct"],
                                wm["fontsize"], wm["angle"])

        elif self._wm_drag_mode == 'resize':
            inv, _ = self._wm_transform_for(wm).inverted()
            local_x = abs(inv.map(pos).x())
            scale = max(0.05, local_x / self._wm_drag_start_resize_lx)
            wm["fontsize"] = max(6, min(300,
                                        round(self._wm_drag_start_fontsize * scale)))
            self.wmChanged.emit(idx, wm["x_pct"], wm["y_pct"],
                                wm["fontsize"], wm["angle"])

        elif self._wm_drag_mode == 'rotate':
            ax = wm["x_pct"] * self.width()
            ay = wm["y_pct"] * self.height()
            cur_scr = math.degrees(math.atan2(pos.y() - ay, pos.x() - ax))
            delta = cur_scr - self._wm_drag_start_scrangle
            na = round(self._wm_drag_start_angle - delta) % 360
            if na > 180:
                na -= 360
            wm["angle"] = na
            self.wmChanged.emit(idx, wm["x_pct"], wm["y_pct"],
                                wm["fontsize"], na)
        self.update()

    # ------------------------------------------------------------------
    # Watermark helpers
    # ------------------------------------------------------------------

    def _wm_qfont_for(self, wm: dict) -> QFont:
        font = QFont("Helvetica")
        font.setPixelSize(max(1, round(wm["fontsize"] * self._zoom)))
        return font

    def _wm_size_for(self, wm: dict) -> tuple[float, float]:
        if wm["type"] == "image":
            pm = wm.get("image_pixmap")
            if pm and pm.height() > 0:
                h = wm["fontsize"] * self._zoom
                w = h * pm.width() / pm.height()
                return w, h
            return wm["fontsize"] * self._zoom, wm["fontsize"] * self._zoom
        fm = QFontMetricsF(self._wm_qfont_for(wm))
        t  = wm["text"].strip() or " "
        return fm.horizontalAdvance(t), fm.height()

    def _wm_transform_for(self, wm: dict) -> QTransform:
        t = QTransform()
        t.translate(wm["x_pct"] * self.width(), wm["y_pct"] * self.height())
        t.rotate(-wm["angle"])
        return t

    def _wm_hit_test_one(self, screen_pos: QPoint, wm: dict,
                         check_handles: bool = True) -> str | None:
        if not wm.get("visible", True):
            return None
        if wm["type"] == "image":
            if wm.get("image_pixmap") is None:
                return None
        else:
            if not wm["text"].strip():
                return None

        text_w, text_h = self._wm_size_for(wm)
        p = WM_PADDING
        inv, ok = self._wm_transform_for(wm).inverted()
        if not ok:
            return None
        loc = inv.map(QPointF(screen_pos))
        lx, ly = loc.x(), loc.y()

        if check_handles:
            rot_y = -(text_h / 2 + p + WM_ROT_OFFSET)
            if math.hypot(lx, ly - rot_y) <= WM_HANDLE_R + 4:
                return 'rotate'
            rh_x = text_w / 2 + p
            if math.hypot(lx - rh_x, ly) <= WM_HANDLE_R + 4:
                return 'resize'

        if (-text_w / 2 - p <= lx <= text_w / 2 + p and
                -text_h / 2 - p <= ly <= text_h / 2 + p):
            return 'move'
        return None

    def _wm_hit_test(self, screen_pos: QPoint) -> tuple:
        if 0 <= self._wm_active_idx < len(self._watermarks):
            hit = self._wm_hit_test_one(
                screen_pos, self._watermarks[self._wm_active_idx],
                check_handles=True)
            if hit:
                return self._wm_active_idx, hit
        for idx, wm in enumerate(self._watermarks):
            if idx == self._wm_active_idx:
                continue
            hit = self._wm_hit_test_one(screen_pos, wm, check_handles=False)
            if hit:
                return idx, hit
        return None, None

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _word_at(self, screen_pos: QPoint) -> int:
        if not self._words:
            return -1
        z  = self._zoom
        px = screen_pos.x() / z
        py = screen_pos.y() / z
        for i, w in enumerate(self._words):
            if w[0] <= px <= w[2] and w[1] <= py <= w[3]:
                return i
        best_i, best_d = 0, float("inf")
        for i, w in enumerate(self._words):
            cx = (w[0] + w[2]) / 2
            cy = (w[1] + w[3]) / 2
            d  = (px - cx) ** 2 + (py - cy) ** 2
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    def _pdf_to_screen(self, rect: fitz.Rect) -> QRectF:
        z = self._zoom
        return QRectF(rect.x0 * z, rect.y0 * z, rect.width * z, rect.height * z)


# ---------------------------------------------------------------------------
# Translation backend
# ---------------------------------------------------------------------------

_LANGUAGES: dict[str, tuple[str, str, str]] = {
    "Chinese (Traditional)": ("ZH",    "zh-TW", "Traditional Chinese"),
    "Chinese (Simplified)":  ("ZH",    "zh-CN", "Simplified Chinese"),
    "English":               ("EN-US", "en",    "English"),
    "Japanese":              ("JA",    "ja",    "Japanese"),
    "Korean":                ("KO",    "ko",    "Korean"),
    "French":                ("FR",    "fr",    "French"),
    "German":                ("DE",    "de",    "German"),
    "Spanish":               ("ES",    "es",    "Spanish"),
}

PROVIDERS = ["Gemini", "DeepL", "Google Translate", "Mock (offline)"]

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


def _friendly_gemini_error(exc: Exception) -> str:
    import re
    msg = str(exc)
    m = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', msg)
    retry = f"  Retry in {m.group(1)} s." if m else ""
    if "429" in msg or "quota" in msg.lower():
        return f"❌ Gemini quota exceeded.{retry}"
    if "400" in msg or "API_KEY" in msg:
        return "❌ Invalid Gemini API key."
    return f"❌ Gemini error: {msg.splitlines()[0][:120]}"


def translate(text: str, provider: str, api_key: str,
              target_lang: str, model: str = "gemini-2.5-flash") -> str:
    if provider == "Mock (offline)" or not text.strip():
        return f"[TRANSLATED] {text}"
    lang_info = _LANGUAGES.get(target_lang, _LANGUAGES["English"])

    if provider == "Gemini":
        try:
            from google import genai as _genai
            from google.genai import types as _types
        except ImportError:
            return "❌ Package missing — run: pip install google-genai"
        if not api_key:
            return "❌ Please enter a Gemini API key."
        try:
            client = _genai.Client(api_key=api_key)
            prompt = (f"Translate the following text to {lang_info[2]}. "
                      f"Return only the translation, no explanation:\n\n{text}")
            resp = client.models.generate_content(
                model=model, contents=prompt,
                config=_types.GenerateContentConfig(temperature=0.2))
            return resp.text.strip()
        except Exception as exc:
            return _friendly_gemini_error(exc)

    elif provider == "DeepL":
        try:
            import deepl as _deepl
        except ImportError:
            return "❌ Package missing — run: pip install deepl"
        if not api_key:
            return "❌ Please enter a DeepL API key."
        return _deepl.Translator(api_key).translate_text(
            text, target_lang=lang_info[0]).text

    elif provider == "Google Translate":
        try:
            import requests as _req
        except ImportError:
            return "❌ Package missing — run: pip install requests"
        if not api_key:
            return "❌ Please enter a Google Cloud API key."
        resp = _req.post(
            "https://translation.googleapis.com/language/translate/v2",
            params={"key": api_key},
            json={"q": text, "target": lang_info[1]},
            timeout=15)
        resp.raise_for_status()
        return resp.json()["data"]["translations"][0]["translatedText"]

    return f"[TRANSLATED] {text}"


# ---------------------------------------------------------------------------
# Translation dialog
# ---------------------------------------------------------------------------

class TranslateDialog(QDialog):
    def __init__(self, original: str, provider: str, api_key: str,
                 target_lang: str, model: str = "gemini-2.5-flash",
                 parent=None):
        super().__init__(parent)
        label = f"{provider}" + (f" ({model})" if provider == "Gemini" else "")
        self.setWindowTitle("Translation")
        self.setMinimumSize(520, 340)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Original:"))
        orig_box = QTextEdit(readOnly=True)
        orig_box.setPlainText(original)
        orig_box.setMaximumHeight(110)
        layout.addWidget(orig_box)

        layout.addWidget(QLabel(f"Translation  ({label} → {target_lang}):"))
        self._trans_box = QTextEdit(readOnly=True)
        self._trans_box.setPlainText("Translating…")
        layout.addWidget(self._trans_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        QApplication.processEvents()
        try:
            result = translate(original, provider, api_key, target_lang, model)
        except Exception as exc:
            result = f"❌ Error: {exc}"
        self._trans_box.setPlainText(result)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Editor")
        self.resize(1200, 800)

        self._doc:           fitz.Document | None = None
        self._current_page:  int   = 0
        self._zoom:          float = 1.0
        self._highlights:    list[dict] = []
        self._watermarks:    list[dict] = []
        self._wm_active_idx: int = -1

        self._build_ui()
        self._apply_dark_theme()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_act = QAction("Open", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._open_pdf)
        toolbar.addAction(open_act)
        toolbar.addSeparator()

        prev_act = QAction("◀  Prev", self)
        prev_act.setShortcut("Left")
        prev_act.triggered.connect(self._prev_page)
        toolbar.addAction(prev_act)

        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, 1)
        self._page_spin.setValue(1)
        self._page_spin.setMinimumWidth(52)
        self._page_spin.setToolTip("頁碼 — 直接輸入跳頁")
        self._page_spin.valueChanged.connect(self._goto_page)
        toolbar.addWidget(self._page_spin)

        self._page_total_label = QLabel("/ —  ")
        toolbar.addWidget(self._page_total_label)

        next_act = QAction("Next  ▶", self)
        next_act.setShortcut("Right")
        next_act.triggered.connect(self._next_page)
        toolbar.addAction(next_act)
        toolbar.addSeparator()

        toolbar.addWidget(QLabel("  Zoom: "))
        self._zoom_spin = QDoubleSpinBox()
        self._zoom_spin.setRange(0.25, 4.0)
        self._zoom_spin.setSingleStep(0.25)
        self._zoom_spin.setValue(1.0)
        self._zoom_spin.setSuffix("×")
        self._zoom_spin.valueChanged.connect(self._change_zoom)
        toolbar.addWidget(self._zoom_spin)
        toolbar.addSeparator()

        export_act = QAction("Export PDF", self)
        export_act.setShortcut("Ctrl+S")
        export_act.triggered.connect(self._export_pdf)
        toolbar.addAction(export_act)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(splitter)

        self._scroll = QScrollArea()
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidgetResizable(False)

        self._page_view = PageView()
        self._page_view.selectionReady.connect(self._on_selection)
        self._page_view.zoomRequested.connect(self._on_zoom_request)
        self._page_view.pageRequested.connect(self._on_page_requested)
        self._page_view.wmChanged.connect(self._on_wm_changed)
        self._page_view.wmClicked.connect(self._on_wm_clicked)
        self._scroll.setWidget(self._page_view)
        splitter.addWidget(self._scroll)

        sidebar = self._build_sidebar()
        sidebar.setFixedWidth(260)
        splitter.addWidget(sidebar)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Open a PDF to get started.")

    def _build_sidebar(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # ---- Watermarks ----
        wm_group = QGroupBox("Watermarks")
        wm_layout = QVBoxLayout(wm_group)
        wm_layout.setContentsMargins(6, 6, 6, 6)
        wm_layout.setSpacing(8)

        self._wm_list = QListWidget()
        self._wm_list.setMaximumHeight(150)
        self._wm_list.setToolTip("選取浮水印以編輯屬性，勾選方塊可切換顯示")
        self._wm_list.currentRowChanged.connect(self._on_wm_list_row_changed)
        self._wm_list.itemChanged.connect(self._on_wm_item_changed)
        wm_layout.addWidget(self._wm_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add")
        add_btn.clicked.connect(self._add_watermark)
        btn_row.addWidget(add_btn)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._delete_watermark)
        btn_row.addWidget(del_btn)
        wm_layout.addLayout(btn_row)

        # --- Properties form (enabled only when a watermark is selected) ---
        self._wm_props = QWidget()
        props_layout = QVBoxLayout(self._wm_props)
        props_layout.setContentsMargins(0, 4, 0, 0)
        props_layout.setSpacing(4)

        # Type selector
        type_row = QFormLayout()
        type_row.setHorizontalSpacing(6)
        self._wm_type = QComboBox()
        self._wm_type.addItems(["文字", "圖片"])
        self._wm_type.currentIndexChanged.connect(self._on_wm_type_changed)
        type_row.addRow("類型:", self._wm_type)
        props_layout.addLayout(type_row)

        # Text section
        self._wm_text_section = QWidget()
        ts_layout = QVBoxLayout(self._wm_text_section)
        ts_layout.setContentsMargins(0, 0, 0, 0)
        ts_layout.setSpacing(2)
        ts_layout.addWidget(QLabel("文字:"))
        self._wm_input = QLineEdit()
        self._wm_input.setPlaceholderText("浮水印文字…")
        self._wm_input.textChanged.connect(self._update_active_wm)
        ts_layout.addWidget(self._wm_input)
        props_layout.addWidget(self._wm_text_section)

        # Image section
        self._wm_image_section = QWidget()
        is_layout = QVBoxLayout(self._wm_image_section)
        is_layout.setContentsMargins(0, 0, 0, 0)
        is_layout.setSpacing(2)
        self._wm_image_btn = QPushButton("選擇圖片…")
        self._wm_image_btn.clicked.connect(self._browse_wm_image)
        is_layout.addWidget(self._wm_image_btn)
        self._wm_image_path_label = QLabel("(未選擇)")
        self._wm_image_path_label.setWordWrap(False)
        self._wm_image_path_label.setMaximumWidth(220)
        is_layout.addWidget(self._wm_image_path_label)
        props_layout.addWidget(self._wm_image_section)
        self._wm_image_section.setVisible(False)

        # Common fields
        common = QFormLayout()
        common.setHorizontalSpacing(6)
        common.setVerticalSpacing(4)

        self._wm_fontsize = QSpinBox()
        self._wm_fontsize.setRange(6, 300)
        self._wm_fontsize.setValue(48)
        self._wm_fontsize.setSuffix(" pt")
        self._wm_fontsize.setToolTip("字型大小 (pt) 或圖片顯示高度 (pt)")
        self._wm_fontsize.valueChanged.connect(self._update_active_wm)
        self._wm_size_label = QLabel("字型大小:")
        common.addRow(self._wm_size_label, self._wm_fontsize)

        self._wm_x = QSpinBox()
        self._wm_x.setRange(0, 100)
        self._wm_x.setValue(50)
        self._wm_x.setSuffix(" %")
        self._wm_x.setToolTip("浮水印水平位置（0% = 左，100% = 右）")
        self._wm_x.valueChanged.connect(self._update_active_wm)
        common.addRow("X:", self._wm_x)

        self._wm_y = QSpinBox()
        self._wm_y.setRange(0, 100)
        self._wm_y.setValue(50)
        self._wm_y.setSuffix(" %")
        self._wm_y.setToolTip("浮水印垂直位置（0% = 上，100% = 下）")
        self._wm_y.valueChanged.connect(self._update_active_wm)
        common.addRow("Y:", self._wm_y)

        self._wm_angle = QSpinBox()
        self._wm_angle.setRange(-180, 180)
        self._wm_angle.setValue(45)
        self._wm_angle.setSuffix(" °")
        self._wm_angle.setToolTip("旋轉角度，−180° ～ 180°，正值為逆時針")
        self._wm_angle.valueChanged.connect(self._update_active_wm)
        common.addRow("角度:", self._wm_angle)

        self._wm_opacity = QSpinBox()
        self._wm_opacity.setRange(10, 100)
        self._wm_opacity.setValue(35)
        self._wm_opacity.setSuffix(" %")
        self._wm_opacity.setToolTip("浮水印不透明度（10% ～ 100%）")
        self._wm_opacity.valueChanged.connect(self._update_active_wm)
        common.addRow("不透明:", self._wm_opacity)

        props_layout.addLayout(common)
        wm_layout.addWidget(self._wm_props)

        # Disable all form inputs initially
        for w in self._wm_editable_widgets():
            w.setEnabled(False)

        drag_hint = QLabel("拖曳頁面可調整位置。\n匯出時套用至每一頁。")
        drag_hint.setToolTip(
            "• 拖曳浮水印本體：移動位置\n"
            "• 拖曳右側藍色圓點：縮放大小\n"
            "• 拖曳上方橘色圓點：旋轉角度")
        wm_layout.addWidget(drag_hint)
        layout.addWidget(wm_group)

        # ---- Translation ----
        tr_group = QGroupBox("Translation")
        tr_layout = QFormLayout(tr_group)
        tr_layout.setHorizontalSpacing(6)
        tr_layout.setVerticalSpacing(4)

        self._tr_provider = QComboBox()
        self._tr_provider.addItems(PROVIDERS)
        self._tr_provider.currentTextChanged.connect(self._on_provider_changed)
        tr_layout.addRow("Provider:", self._tr_provider)

        self._tr_model_label = QLabel("Model:")
        self._tr_model = QComboBox()
        self._tr_model.addItems(GEMINI_MODELS)
        tr_layout.addRow(self._tr_model_label, self._tr_model)

        self._tr_api_key = QLineEdit()
        self._tr_api_key.setPlaceholderText("API key…")
        self._tr_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        tr_layout.addRow("API Key:", self._tr_api_key)

        self._tr_lang = QComboBox()
        self._tr_lang.addItems(list(_LANGUAGES.keys()))
        tr_layout.addRow("Target:", self._tr_lang)

        layout.addWidget(tr_group)
        self._on_provider_changed(self._tr_provider.currentText())

        # ---- Highlights ----
        hl_group = QGroupBox("Highlights")
        hl_layout = QVBoxLayout(hl_group)
        self._hl_list = QListWidget()
        self._hl_list.setMaximumHeight(140)
        self._hl_list.itemDoubleClicked.connect(self._jump_to_highlight)
        hl_layout.addWidget(self._hl_list)
        del_hl_btn = QPushButton("Delete Selected")
        del_hl_btn.setObjectName("dangerBtn")
        del_hl_btn.clicked.connect(self._delete_highlight)
        hl_layout.addWidget(del_hl_btn)
        layout.addWidget(hl_group)

        layout.addStretch()
        return container

    def _wm_editable_widgets(self):
        """Widgets to enable/disable based on whether a watermark is selected."""
        return (self._wm_type, self._wm_input, self._wm_image_btn,
                self._wm_fontsize, self._wm_x, self._wm_y, self._wm_angle,
                self._wm_opacity)

    def _wm_signal_widgets(self):
        """Widgets whose signals to block during programmatic value updates."""
        return (self._wm_type, self._wm_input,
                self._wm_fontsize, self._wm_x, self._wm_y, self._wm_angle,
                self._wm_opacity)

    # ------------------------------------------------------------------
    # Translation provider
    # ------------------------------------------------------------------

    def _on_provider_changed(self, provider: str) -> None:
        gemini = provider == "Gemini"
        self._tr_model_label.setVisible(gemini)
        self._tr_model.setVisible(gemini)

    # ------------------------------------------------------------------
    # Watermark list management
    # ------------------------------------------------------------------

    def _add_watermark(self) -> None:
        wm = _default_watermark()
        self._watermarks.append(wm)
        idx = len(self._watermarks) - 1
        item = QListWidgetItem(f"Watermark {idx + 1}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self._wm_list.blockSignals(True)
        self._wm_list.addItem(item)
        self._wm_list.blockSignals(False)
        self._wm_list.setCurrentRow(idx)

    def _delete_watermark(self) -> None:
        idx = self._wm_active_idx
        if idx < 0 or idx >= len(self._watermarks):
            return
        del self._watermarks[idx]
        self._wm_list.blockSignals(True)
        self._wm_list.takeItem(idx)
        self._wm_list.blockSignals(False)
        for i in range(idx, self._wm_list.count()):
            self._refresh_wm_list_item(i)
        new_idx = min(idx, len(self._watermarks) - 1)
        if new_idx >= 0:
            self._wm_list.setCurrentRow(new_idx)
        else:
            self._wm_active_idx = -1
            for w in self._wm_editable_widgets():
                w.setEnabled(False)
            self._wm_text_section.setVisible(False)
            self._wm_image_section.setVisible(False)
            self._page_view.set_watermarks(self._watermarks, -1)

    def _refresh_wm_list_item(self, idx: int) -> None:
        item = self._wm_list.item(idx)
        if item is None or idx >= len(self._watermarks):
            return
        wm = self._watermarks[idx]
        if wm["type"] == "image":
            path = wm.get("image_path", "")
            label = os.path.basename(path) if path else f"Watermark {idx + 1}"
        else:
            text = wm["text"].strip()
            label = text if text else f"Watermark {idx + 1}"
        self._wm_list.blockSignals(True)
        item.setText(label)
        item.setCheckState(
            Qt.CheckState.Checked if wm.get("visible", True)
            else Qt.CheckState.Unchecked)
        self._wm_list.blockSignals(False)

    def _on_wm_item_changed(self, item: QListWidgetItem) -> None:
        """Checkbox toggled — update visibility."""
        row = self._wm_list.row(item)
        if 0 <= row < len(self._watermarks):
            self._watermarks[row]["visible"] = (
                item.checkState() == Qt.CheckState.Checked)
            self._page_view.set_watermarks(self._watermarks, self._wm_active_idx)

    def _on_wm_list_row_changed(self, row: int) -> None:
        self._wm_active_idx = row
        if row < 0 or row >= len(self._watermarks):
            for w in self._wm_editable_widgets():
                w.setEnabled(False)
            self._wm_text_section.setVisible(False)
            self._wm_image_section.setVisible(False)
            self._page_view.set_watermarks(self._watermarks, -1)
            return

        wm = self._watermarks[row]
        for w in self._wm_editable_widgets():
            w.setEnabled(True)

        is_text = (wm["type"] == "text")
        self._wm_text_section.setVisible(is_text)
        self._wm_image_section.setVisible(not is_text)
        self._wm_size_label.setText("字型大小:" if is_text else "高度:")

        for w in self._wm_signal_widgets():
            w.blockSignals(True)
        self._wm_type.setCurrentIndex(0 if is_text else 1)
        self._wm_input.setText(wm["text"])
        self._wm_fontsize.setValue(wm["fontsize"])
        self._wm_x.setValue(round(wm["x_pct"] * 100))
        self._wm_y.setValue(round(wm["y_pct"] * 100))
        self._wm_angle.setValue(wm["angle"])
        self._wm_opacity.setValue(round(wm.get("opacity", 0.35) * 100))
        for w in self._wm_signal_widgets():
            w.blockSignals(False)

        path = wm.get("image_path", "")
        name = os.path.basename(path) if path else "(未選擇)"
        self._wm_image_path_label.setText(
            self._elide_label_text(self._wm_image_path_label, name))
        self._wm_image_path_label.setToolTip(path if path else "")

        self._page_view.set_watermarks(self._watermarks, row)

    def _on_wm_type_changed(self, index: int) -> None:
        is_text = (index == 0)
        self._wm_text_section.setVisible(is_text)
        self._wm_image_section.setVisible(not is_text)
        self._wm_size_label.setText("字型大小:" if is_text else "高度:")
        self._update_active_wm()

    def _update_active_wm(self) -> None:
        idx = self._wm_active_idx
        if idx < 0 or idx >= len(self._watermarks):
            return
        wm = self._watermarks[idx]
        wm["type"]     = "text" if self._wm_type.currentIndex() == 0 else "image"
        wm["text"]     = self._wm_input.text()
        wm["fontsize"] = self._wm_fontsize.value()
        wm["x_pct"]    = self._wm_x.value() / 100.0
        wm["y_pct"]    = self._wm_y.value() / 100.0
        wm["angle"]    = self._wm_angle.value()
        wm["opacity"]  = self._wm_opacity.value() / 100.0
        self._refresh_wm_list_item(idx)
        self._page_view.set_watermarks(self._watermarks, idx)

    def _on_wm_changed(self, idx: int, x_pct: float, y_pct: float,
                       fontsize: int, angle: int) -> None:
        if idx < 0 or idx >= len(self._watermarks):
            return
        wm = self._watermarks[idx]
        wm["x_pct"]    = x_pct
        wm["y_pct"]    = y_pct
        wm["fontsize"] = fontsize
        wm["angle"]    = angle
        if idx == self._wm_active_idx:
            for w in (self._wm_x, self._wm_y, self._wm_fontsize, self._wm_angle):
                w.blockSignals(True)
            self._wm_x.setValue(round(x_pct * 100))
            self._wm_y.setValue(round(y_pct * 100))
            self._wm_fontsize.setValue(fontsize)
            self._wm_angle.setValue(angle)
            for w in (self._wm_x, self._wm_y, self._wm_fontsize, self._wm_angle):
                w.blockSignals(False)

    def _on_wm_clicked(self, idx: int) -> None:
        self._wm_list.setCurrentRow(idx)

    def _browse_wm_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇圖片", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp)")
        if not path:
            return
        self._status.showMessage("處理圖片中…")
        QApplication.processEvents()
        pixmap, img_bytes, err = _process_watermark_image(path)
        if err:
            self._status.showMessage(err, 4000)
            return
        idx = self._wm_active_idx
        if 0 <= idx < len(self._watermarks):
            wm = self._watermarks[idx]
            wm["image_path"]   = path
            wm["image_pixmap"] = pixmap
            wm["image_bytes"]  = img_bytes
            name = os.path.basename(path)
            self._wm_image_path_label.setText(
                self._elide_label_text(self._wm_image_path_label, name))
            self._wm_image_path_label.setToolTip(path)
            self._refresh_wm_list_item(idx)
            self._page_view.set_watermarks(self._watermarks, idx)
            self._status.showMessage(f"圖片已載入: {name}", 4000)

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _open_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        self._doc = fitz.open(path)
        self._current_page = 0
        self._highlights.clear()
        self._hl_list.clear()
        self._render_current_page()
        self._status.showMessage(f"Opened: {path}")

    def _export_pdf(self) -> None:
        if self._doc is None:
            self._status.showMessage("No document open.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        buf = io.BytesIO()
        self._doc.save(buf)
        buf.seek(0)
        doc2 = fitz.open("pdf", buf.read())
        total = len(doc2)

        progress = QProgressDialog("準備中…", "取消", 0, total + 1, self)
        progress.setWindowTitle("匯出 PDF")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QApplication.processEvents()

        for page_num in range(total):
            if progress.wasCanceled():
                doc2.close()
                self._status.showMessage("匯出已取消。", 3000)
                return

            progress.setLabelText(f"處理第 {page_num + 1} / {total} 頁…")
            progress.setValue(page_num + 1)
            QApplication.processEvents()

            page = doc2[page_num]
            pr   = page.rect

            for wm in self._watermarks:
                if not wm.get("visible", True):
                    continue

                opacity = wm.get("opacity", 0.35)

                if wm["type"] == "text":
                    if not wm["text"].strip():
                        continue
                    font   = fitz.Font("helv")
                    fs     = wm["fontsize"]
                    text_w = font.text_length(wm["text"], fontsize=fs)
                    anchor = fitz.Point(pr.width  * wm["x_pct"],
                                        pr.height * wm["y_pct"])
                    start  = fitz.Point(anchor.x - text_w / 2, anchor.y)
                    tw = fitz.TextWriter(pr)
                    tw.append(start, wm["text"], fontsize=fs, font=font)
                    tw.write_text(page,
                                  color=(0.6, 0.6, 0.6),
                                  opacity=opacity,
                                  morph=(anchor, fitz.Matrix(wm["angle"])),
                                  overlay=True)

                elif wm["type"] == "image":
                    img_bytes = wm.get("image_bytes")
                    if not img_bytes:
                        continue
                    pm = wm.get("image_pixmap")
                    h  = float(wm["fontsize"])
                    w  = h * (pm.width() / pm.height()) if (pm and pm.height()) else h
                    # Pre-rotate image for arbitrary angles
                    angle = wm["angle"]
                    data  = _rotate_image_bytes(img_bytes, angle)
                    # Apply opacity to alpha channel
                    try:
                        from PIL import Image as _PIL
                        import numpy as _np
                        rotated = _PIL.open(io.BytesIO(data)).convert("RGBA")
                        rot_w, rot_h = rotated.size
                        if rot_h > 0:
                            w = h * rot_w / rot_h
                        arr = _np.array(rotated, dtype=_np.float32)
                        arr[:, :, 3] *= opacity
                        rotated = _PIL.fromarray(arr.astype(_np.uint8))
                        buf = io.BytesIO()
                        rotated.save(buf, format="PNG")
                        data = buf.getvalue()
                    except Exception:
                        pass
                    cx = pr.width  * wm["x_pct"]
                    cy = pr.height * wm["y_pct"]
                    rect = fitz.Rect(cx - w / 2, cy - h / 2,
                                     cx + w / 2, cy + h / 2)
                    page.insert_image(rect, stream=data, overlay=True)

            for hl in self._highlights:
                if hl["page"] == page_num:
                    page.add_highlight_annot(hl["rect"]).update()

        progress.setLabelText("儲存檔案…")
        progress.setValue(total + 1)
        QApplication.processEvents()

        doc2.save(path, garbage=4, deflate=True)
        doc2.close()
        progress.close()
        self._status.showMessage(f"Exported to: {path}", 5000)
        QMessageBox.information(
            self, "匯出完成",
            f"PDF 已成功匯出至：\n{path}")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _prev_page(self) -> None:
        if self._doc and self._current_page > 0:
            self._current_page -= 1
            self._render_current_page()

    def _next_page(self) -> None:
        if self._doc and self._current_page < len(self._doc) - 1:
            self._current_page += 1
            self._render_current_page()

    def _on_page_requested(self, direction: int) -> None:
        """Flip page when wheel reaches the top or bottom of the scroll area."""
        if self._doc is None:
            return
        sb = self._scroll.verticalScrollBar()
        if direction > 0:
            # Scrolling down — flip to next page only at the bottom
            if sb.value() >= sb.maximum():
                self._next_page()
                sb.setValue(0)
        else:
            # Scrolling up — flip to prev page only at the top
            if sb.value() <= sb.minimum():
                self._prev_page()
                # Defer scroll-to-bottom so the new page has time to lay out
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def _change_zoom(self, value: float) -> None:
        self._zoom = value
        self._render_current_page()

    def _on_zoom_request(self, direction: int) -> None:
        new = round(self._zoom_spin.value() * 4 + direction) / 4
        self._zoom_spin.setValue(max(0.25, min(4.0, new)))

    def _goto_page(self, page_num: int) -> None:
        if self._doc is None:
            return
        target = page_num - 1
        if 0 <= target < len(self._doc) and target != self._current_page:
            self._current_page = target
            self._render_current_page()

    def _render_current_page(self) -> None:
        if self._doc is None:
            return
        page = self._doc[self._current_page]
        self._page_view.set_page(
            page, self._current_page, self._zoom, self._highlights)
        self._page_spin.blockSignals(True)
        self._page_spin.setRange(1, len(self._doc))
        self._page_spin.setValue(self._current_page + 1)
        self._page_spin.blockSignals(False)
        self._page_total_label.setText(f"/ {len(self._doc)}  ")
        self._page_view.set_watermarks(self._watermarks, self._wm_active_idx)

    # ------------------------------------------------------------------
    # Selection → context menu
    # ------------------------------------------------------------------

    def _on_selection(self, text: str, word_rects: list,
                      global_pos: QPoint) -> None:
        menu   = QMenu(self)
        hl_act = menu.addAction("Highlight")
        tr_act = menu.addAction("Translate")
        chosen = menu.exec(global_pos)
        if chosen == hl_act:
            self._add_highlights(word_rects, text)
        elif chosen == tr_act:
            TranslateDialog(
                text,
                provider=self._tr_provider.currentText(),
                api_key=self._tr_api_key.text().strip(),
                target_lang=self._tr_lang.currentText(),
                model=self._tr_model.currentText(),
                parent=self,
            ).exec()

    def _add_highlights(self, rects: list[fitz.Rect], text: str) -> None:
        for rect in rects:
            self._highlights.append({
                "page": self._current_page,
                "rect": rect,
                "text": text,
            })
        self._page_view.update_highlights(self._highlights)
        snippet = text[:40] + ("…" if len(text) > 40 else "")
        item = QListWidgetItem(f"p{self._current_page + 1}: {snippet}")
        item.setData(Qt.ItemDataRole.UserRole,
                     len(self._highlights) - len(rects))
        self._hl_list.addItem(item)

    def _jump_to_highlight(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        if 0 <= idx < len(self._highlights):
            target = self._highlights[idx]["page"]
            if target != self._current_page:
                self._current_page = target
                self._render_current_page()

    def _delete_highlight(self) -> None:
        selected = self._hl_list.selectedItems()
        if not selected:
            return
        item      = selected[0]
        row       = self._hl_list.row(item)
        start_idx = item.data(Qt.ItemDataRole.UserRole)
        if row + 1 < self._hl_list.count():
            end_idx = self._hl_list.item(row + 1).data(Qt.ItemDataRole.UserRole)
        else:
            end_idx = len(self._highlights)
        del self._highlights[start_idx:end_idx]
        self._hl_list.takeItem(row)
        offset = end_idx - start_idx
        for i in range(row, self._hl_list.count()):
            it = self._hl_list.item(i)
            it.setData(Qt.ItemDataRole.UserRole,
                       it.data(Qt.ItemDataRole.UserRole) - offset)
        self._page_view.update_highlights(self._highlights)

    # ------------------------------------------------------------------
    # Dark theme
    # ------------------------------------------------------------------

    @staticmethod
    def _elide_label_text(label: QLabel, text: str) -> str:
        """Return text elided with '…' to fit inside label's maximum width."""
        fm = QFontMetrics(label.font())
        max_w = label.maximumWidth()
        if max_w <= 0:
            max_w = 200
        return fm.elidedText(text, Qt.TextElideMode.ElideRight, max_w - 4)

    def _apply_dark_theme(self) -> None:
        QApplication.instance().setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e1e; color: #d4d4d4;
            }
            QToolBar {
                background-color: #2d2d2d;
                border-bottom: 1px solid #444; spacing: 4px;
            }
            QToolBar QToolButton {
                background-color: #3c3c3c; border: 1px solid #555;
                border-radius: 3px; padding: 3px 8px; color: #d4d4d4;
            }
            QToolBar QToolButton:hover { background-color: #505050; }
            QScrollArea { background-color: #333; border: none; }
            QGroupBox {
                border: 1px solid #555; border-radius: 4px;
                margin-top: 8px; font-weight: bold; color: #aaa;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 8px; padding: 0 4px;
            }
            QLineEdit, QDoubleSpinBox, QSpinBox {
                background-color: #3c3c3c; border: 1px solid #555;
                border-radius: 3px; padding: 3px; color: #d4d4d4;
            }
            QPushButton {
                background-color: #3c3c3c; border: 1px solid #555;
                border-radius: 3px; padding: 4px 10px; color: #d4d4d4;
            }
            QPushButton:hover { background-color: #505050; }
            QListWidget {
                background-color: #2d2d2d; border: 1px solid #444;
                color: #d4d4d4;
            }
            QListWidget::item:selected { background-color: #094771; }
            QStatusBar { background-color: #007acc; color: white; }
            QMenu {
                background-color: #2d2d2d; border: 1px solid #555;
                color: #d4d4d4;
            }
            QMenu::item:selected { background-color: #094771; }
            QDialog { background-color: #1e1e1e; }
            QTextEdit {
                background-color: #2d2d2d; border: 1px solid #444;
                color: #d4d4d4;
            }
            QLabel { color: #d4d4d4; }
            QComboBox {
                background-color: #3c3c3c; border: 1px solid #555;
                border-radius: 3px; padding: 3px; color: #d4d4d4;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid #aaa;
                width: 0; height: 0;
            }
            QComboBox QAbstractItemView {
                background-color: #2d2d2d; color: #d4d4d4;
                selection-background-color: #094771;
            }
            QPushButton#dangerBtn { color: #f48771; }
            QPushButton#dangerBtn:hover { background-color: #5a2a2a; }
        """)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Editor")

    default_font = app.font()
    default_font.setFamilies(["Ubuntu", "Microsoft JhengHei", "sans-serif"])
    app.setFont(default_font)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
