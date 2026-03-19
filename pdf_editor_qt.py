"""PyQt6 native PDF editor with PyMuPDF rendering."""
import io
import math
import sys

import fitz  # PyMuPDF
from PyQt6.QtCore import (
    QPoint, QPointF, QRectF, Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QFontMetricsF, QImage, QPainter,
    QPen, QPixmap, QTransform,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QPushButton, QScrollArea, QSpinBox, QSplitter,
    QStatusBar, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

HIGHLIGHT_COLOR = QColor(255, 220, 0, 110)
SELECTION_COLOR = QColor(100, 149, 237, 80)
WM_HANDLE_R   = 6    # handle circle radius (px)
WM_PADDING    = 4    # bounding-box padding (px)
WM_ROT_OFFSET = 24   # pixels above bounding box for rotate handle


# ---------------------------------------------------------------------------
# Page view widget
# ---------------------------------------------------------------------------

class PageView(QWidget):
    """Renders one PDF page; handles text selection and watermark interaction."""

    selectionReady = pyqtSignal(str, list, QPoint)   # text, word_rects, global_pos
    zoomRequested  = pyqtSignal(int)                  # +1 / -1
    wmChanged      = pyqtSignal(float, float, int, int)  # x_pct, y_pct, fontsize, angle

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._page:   fitz.Page | None = None
        self._zoom:   float = 1.0
        self._highlights: list[dict] = []
        self._page_index: int = 0

        # Word-level text selection
        self._words:      list  = []
        self._sel_start:  int   = -1
        self._sel_end:    int   = -1
        self._sel_active: bool  = False

        # Watermark overlay
        self._wm_text:     str   = ""
        self._wm_x_pct:    float = 0.5
        self._wm_y_pct:    float = 0.5
        self._wm_fontsize: int   = 48
        self._wm_angle:    int   = 45

        # Watermark drag state
        self._wm_drag_mode:           str | None  = None   # 'move'|'resize'|'rotate'
        self._wm_drag_origin:         QPointF | None = None
        self._wm_drag_start_x_pct:    float = 0.5
        self._wm_drag_start_y_pct:    float = 0.5
        self._wm_drag_start_fontsize: int   = 48
        self._wm_drag_start_angle:    int   = 45
        self._wm_drag_start_scrangle: float = 0.0   # screen-angle at drag start
        self._wm_drag_start_resize_lx: float = 1.0  # local-x of resize handle at start

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

    def set_watermark(self, text: str, x_pct: float, y_pct: float,
                      fontsize: int, angle: int) -> None:
        self._wm_text     = text
        self._wm_x_pct    = x_pct
        self._wm_y_pct    = y_pct
        self._wm_fontsize = fontsize
        self._wm_angle    = angle
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
        if self._pixmap:
            painter.drawPixmap(0, 0, self._pixmap)

        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)

        # Stored highlights
        for hl in self._highlights:
            if hl["page"] == self._page_index:
                painter.fillRect(self._pdf_to_screen(hl["rect"]),
                                 HIGHLIGHT_COLOR)

        # Text selection
        if self._sel_start >= 0 and self._sel_end >= 0:
            lo, hi = sorted((self._sel_start, self._sel_end))
            for i in range(lo, hi + 1):
                painter.fillRect(
                    self._pdf_to_screen(fitz.Rect(self._words[i][:4])),
                    SELECTION_COLOR)

        # Watermark overlay
        if self._wm_text:
            self._paint_watermark(painter)

        painter.end()

    def _paint_watermark(self, painter: QPainter) -> None:
        text_w, text_h = self._wm_size()
        p = WM_PADDING
        ax = self._wm_x_pct * self.width()
        ay = self._wm_y_pct * self.height()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(ax, ay)
        painter.rotate(-self._wm_angle)   # Qt CW → negate for CCW (PyMuPDF convention)

        # Text (semi-transparent grey)
        painter.setOpacity(0.40)
        painter.setFont(self._wm_qfont())
        painter.setPen(QColor(180, 180, 180))
        painter.drawText(
            QRectF(-text_w / 2, -text_h / 2, text_w, text_h),
            Qt.AlignmentFlag.AlignCenter,
            self._wm_text,
        )
        painter.setOpacity(1.0)

        # Dashed bounding box
        box = QRectF(-text_w / 2 - p, -text_h / 2 - p,
                     text_w + p * 2, text_h + p * 2)
        painter.setPen(QPen(QColor(100, 149, 237, 180), 1,
                            Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box)

        # Resize handle – right-centre of box (blue)
        rh = QPointF(box.right(), 0.0)
        painter.setPen(QPen(QColor(100, 149, 237)))
        painter.setBrush(QColor(100, 149, 237))
        painter.drawEllipse(rh, WM_HANDLE_R, WM_HANDLE_R)

        # Rotate handle – above top-centre (orange)
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

        # Watermark interaction takes priority
        if self._wm_text:
            hit = self._wm_hit_test(pos)
            if hit:
                self._wm_drag_mode          = hit
                self._wm_drag_origin        = event.position()
                self._wm_drag_start_x_pct   = self._wm_x_pct
                self._wm_drag_start_y_pct   = self._wm_y_pct
                self._wm_drag_start_fontsize = self._wm_fontsize
                self._wm_drag_start_angle   = self._wm_angle

                ax = self._wm_x_pct * self.width()
                ay = self._wm_y_pct * self.height()
                self._wm_drag_start_scrangle = math.degrees(
                    math.atan2(event.position().y() - ay,
                               event.position().x() - ax))

                if hit == 'resize':
                    inv, _ = self._wm_transform().inverted()
                    self._wm_drag_start_resize_lx = max(
                        1.0, abs(inv.map(event.position()).x()))

                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                return

        # Text selection
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

        # Hover cursor
        if self._wm_text:
            hit = self._wm_hit_test(event.position().toPoint())
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
            self.setCursor(Qt.CursorShape.IBeamCursor)
            return

        if self._sel_active:
            self._sel_active = False
            lo, hi = sorted((self._sel_start, self._sel_end))
            if lo >= 0 and hi >= 0:
                sel = self._words[lo:hi + 1]
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
            event.ignore()

    # ------------------------------------------------------------------
    # Watermark drag
    # ------------------------------------------------------------------

    def _handle_wm_drag(self, pos: QPointF) -> None:
        if self._wm_drag_mode == 'move':
            dx = (pos.x() - self._wm_drag_origin.x()) / self.width()
            dy = (pos.y() - self._wm_drag_origin.y()) / self.height()
            nx = max(0.0, min(1.0, self._wm_drag_start_x_pct + dx))
            ny = max(0.0, min(1.0, self._wm_drag_start_y_pct + dy))
            self._wm_x_pct = nx
            self._wm_y_pct = ny
            self.wmChanged.emit(nx, ny, self._wm_fontsize, self._wm_angle)

        elif self._wm_drag_mode == 'resize':
            inv, _ = self._wm_transform().inverted()
            local_x = abs(inv.map(pos).x())
            scale = max(0.05, local_x / self._wm_drag_start_resize_lx)
            nfs = max(6, min(300, round(self._wm_drag_start_fontsize * scale)))
            self._wm_fontsize = nfs
            self.wmChanged.emit(self._wm_x_pct, self._wm_y_pct,
                                nfs, self._wm_angle)

        elif self._wm_drag_mode == 'rotate':
            ax = self._wm_x_pct * self.width()
            ay = self._wm_y_pct * self.height()
            cur_scr = math.degrees(math.atan2(pos.y() - ay, pos.x() - ax))
            delta = cur_scr - self._wm_drag_start_scrangle
            # Screen CW delta → CCW in PDF convention → subtract
            na = round(self._wm_drag_start_angle - delta) % 360
            if na > 180:
                na -= 360
            self._wm_angle = na
            self.wmChanged.emit(self._wm_x_pct, self._wm_y_pct,
                                self._wm_fontsize, na)

        self.update()

    # ------------------------------------------------------------------
    # Watermark helpers
    # ------------------------------------------------------------------

    def _wm_qfont(self) -> QFont:
        font = QFont("Helvetica")
        font.setPixelSize(max(1, round(self._wm_fontsize * self._zoom)))
        return font

    def _wm_size(self) -> tuple[float, float]:
        """Returns (text_w, text_h) in screen pixels."""
        fm = QFontMetricsF(self._wm_qfont())
        return fm.horizontalAdvance(self._wm_text), fm.height()

    def _wm_transform(self) -> QTransform:
        """Maps watermark local coords → screen coords."""
        t = QTransform()
        t.translate(self._wm_x_pct * self.width(),
                    self._wm_y_pct * self.height())
        t.rotate(-self._wm_angle)
        return t

    def _wm_hit_test(self, screen_pos: QPoint) -> str | None:
        if not self._wm_text:
            return None
        text_w, text_h = self._wm_size()
        p = WM_PADDING

        inv, ok = self._wm_transform().inverted()
        if not ok:
            return None
        loc = inv.map(QPointF(screen_pos))
        lx, ly = loc.x(), loc.y()

        # Rotate handle
        rot_y = -(text_h / 2 + p + WM_ROT_OFFSET)
        if math.hypot(lx, ly - rot_y) <= WM_HANDLE_R + 4:
            return 'rotate'

        # Resize handle (right-centre)
        rh_x = text_w / 2 + p
        if math.hypot(lx - rh_x, ly) <= WM_HANDLE_R + 4:
            return 'resize'

        # Body
        if (-text_w / 2 - p <= lx <= text_w / 2 + p and
                -text_h / 2 - p <= ly <= text_h / 2 + p):
            return 'move'

        return None

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

# Maps display name → (deepl_code, google_code, gemini_name)
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
    "gemini-2.5-flash",      # recommended — best free tier balance
    "gemini-2.5-flash-lite", # lightest 2.5
    "gemini-2.0-flash",      # stable 2.0
    "gemini-2.0-flash-lite", # lite 2.0
]


def _friendly_gemini_error(exc: Exception) -> str:
    """Turn a Gemini API exception into a short, readable message."""
    msg = str(exc)
    # Extract retry delay if present
    import re
    m = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', msg)
    retry = f"  Retry in {m.group(1)} s." if m else ""
    # Quota exceeded
    if "429" in msg or "quota" in msg.lower():
        return f"❌ Gemini quota exceeded.{retry}"
    # API key invalid
    if "400" in msg or "API_KEY" in msg:
        return "❌ Invalid Gemini API key."
    # Generic fallback — first line only
    first_line = msg.splitlines()[0][:120]
    return f"❌ Gemini error: {first_line}"


def translate(text: str, provider: str, api_key: str,
              target_lang: str, model: str = "gemini-2.5-flash") -> str:
    """Call the selected translation provider and return the translated string."""
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
            prompt = (
                f"Translate the following text to {lang_info[2]}. "
                f"Return only the translation, no explanation:\n\n{text}"
            )
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=_types.GenerateContentConfig(temperature=0.2),
            )
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
        translator = _deepl.Translator(api_key)
        return translator.translate_text(text, target_lang=lang_info[0]).text

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
            timeout=15,
        )
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

        # Run translation (synchronous — dialog shows "Translating…" first)
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

        self._doc:          fitz.Document | None = None
        self._current_page: int   = 0
        self._zoom:         float = 1.0
        self._highlights:   list[dict] = []

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

        self._page_label = QLabel("  —  ")
        toolbar.addWidget(self._page_label)

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

        # Central splitter
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(splitter)

        self._scroll = QScrollArea()
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidgetResizable(False)

        self._page_view = PageView()
        self._page_view.selectionReady.connect(self._on_selection)
        self._page_view.zoomRequested.connect(self._on_zoom_request)
        self._page_view.wmChanged.connect(self._on_wm_changed)
        self._scroll.setWidget(self._page_view)
        splitter.addWidget(self._scroll)

        sidebar = self._build_sidebar()
        sidebar.setFixedWidth(240)
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

        # ---- Watermark ----
        wm_group = QGroupBox("Watermark")
        wm_layout = QVBoxLayout(wm_group)

        self._wm_input = QLineEdit()
        self._wm_input.setPlaceholderText("Watermark text…")
        self._wm_input.textChanged.connect(self._update_wm_preview)
        wm_layout.addWidget(self._wm_input)

        form = QFormLayout()
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(4)

        self._wm_fontsize = QSpinBox()
        self._wm_fontsize.setRange(6, 300)
        self._wm_fontsize.setValue(48)
        self._wm_fontsize.setSuffix(" pt")
        self._wm_fontsize.valueChanged.connect(self._update_wm_preview)
        form.addRow("Size:", self._wm_fontsize)

        self._wm_x = QSpinBox()
        self._wm_x.setRange(0, 100)
        self._wm_x.setValue(50)
        self._wm_x.setSuffix(" %")
        self._wm_x.valueChanged.connect(self._update_wm_preview)
        form.addRow("X:", self._wm_x)

        self._wm_y = QSpinBox()
        self._wm_y.setRange(0, 100)
        self._wm_y.setValue(50)
        self._wm_y.setSuffix(" %")
        self._wm_y.valueChanged.connect(self._update_wm_preview)
        form.addRow("Y:", self._wm_y)

        self._wm_angle = QSpinBox()
        self._wm_angle.setRange(-180, 180)
        self._wm_angle.setValue(45)
        self._wm_angle.setSuffix(" °")
        self._wm_angle.valueChanged.connect(self._update_wm_preview)
        form.addRow("Angle:", self._wm_angle)

        wm_layout.addLayout(form)
        wm_layout.addWidget(QLabel("Drag on page to reposition.\nExported to every page."))
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
        # Sync initial visibility
        self._on_provider_changed(self._tr_provider.currentText())

        # ---- Highlights ----
        hl_group = QGroupBox("Highlights")
        hl_layout = QVBoxLayout(hl_group)
        self._hl_list = QListWidget()
        self._hl_list.itemDoubleClicked.connect(self._jump_to_highlight)
        hl_layout.addWidget(self._hl_list)
        del_btn = QPushButton("Delete Selected")
        del_btn.clicked.connect(self._delete_highlight)
        hl_layout.addWidget(del_btn)
        layout.addWidget(hl_group)

        layout.addStretch()
        return container

    # ------------------------------------------------------------------
    # Translation provider
    # ------------------------------------------------------------------

    def _on_provider_changed(self, provider: str) -> None:
        gemini = provider == "Gemini"
        self._tr_model_label.setVisible(gemini)
        self._tr_model.setVisible(gemini)

    # ------------------------------------------------------------------
    # Watermark preview sync
    # ------------------------------------------------------------------

    def _update_wm_preview(self) -> None:
        self._page_view.set_watermark(
            self._wm_input.text().strip(),
            self._wm_x.value() / 100.0,
            self._wm_y.value() / 100.0,
            self._wm_fontsize.value(),
            self._wm_angle.value(),
        )

    def _on_wm_changed(self, x_pct: float, y_pct: float,
                       fontsize: int, angle: int) -> None:
        """PageView dragged the watermark — sync sidebar widgets."""
        widgets = (self._wm_x, self._wm_y, self._wm_fontsize, self._wm_angle)
        for w in widgets:
            w.blockSignals(True)
        self._wm_x.setValue(round(x_pct * 100))
        self._wm_y.setValue(round(y_pct * 100))
        self._wm_fontsize.setValue(fontsize)
        self._wm_angle.setValue(angle)
        for w in widgets:
            w.blockSignals(False)

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

        watermark  = self._wm_input.text().strip()
        wm_fontsize = self._wm_fontsize.value()
        wm_x_pct   = self._wm_x.value() / 100.0
        wm_y_pct   = self._wm_y.value() / 100.0
        wm_angle   = self._wm_angle.value()

        for page_num in range(len(doc2)):
            page = doc2[page_num]

            if watermark:
                pr     = page.rect
                font   = fitz.Font("helv")
                text_w = font.text_length(watermark, fontsize=wm_fontsize)
                anchor = fitz.Point(pr.width * wm_x_pct, pr.height * wm_y_pct)
                start  = fitz.Point(anchor.x - text_w / 2, anchor.y)
                tw = fitz.TextWriter(page.rect)
                tw.append(start, watermark, fontsize=wm_fontsize, font=font)
                tw.write_text(page, color=(0.75, 0.75, 0.75),
                              morph=(anchor, fitz.Matrix(wm_angle)))

            for hl in self._highlights:
                if hl["page"] == page_num:
                    page.add_highlight_annot(hl["rect"]).update()

        doc2.save(path, garbage=4, deflate=True)
        doc2.close()
        self._status.showMessage(f"Exported to: {path}")

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

    def _change_zoom(self, value: float) -> None:
        self._zoom = value
        self._render_current_page()

    def _on_zoom_request(self, direction: int) -> None:
        new = round(self._zoom_spin.value() * 4 + direction) / 4
        self._zoom_spin.setValue(max(0.25, min(4.0, new)))

    def _render_current_page(self) -> None:
        if self._doc is None:
            return
        page = self._doc[self._current_page]
        self._page_view.set_page(
            page, self._current_page, self._zoom, self._highlights)
        self._page_label.setText(
            f"  {self._current_page + 1} / {len(self._doc)}  ")
        # Reapply watermark overlay after re-render
        self._update_wm_preview()

    # ------------------------------------------------------------------
    # Selection → context menu
    # ------------------------------------------------------------------

    def _on_selection(self, text: str, word_rects: list,
                      global_pos: QPoint) -> None:
        menu    = QMenu(self)
        hl_act  = menu.addAction("Highlight")
        tr_act  = menu.addAction("Translate")
        chosen  = menu.exec(global_pos)
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

    # ------------------------------------------------------------------
    # Sidebar interactions
    # ------------------------------------------------------------------

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
        """)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Editor")

    # Ensure CJK characters render correctly by adding a fallback font
    default_font = app.font()
    default_font.setFamilies(["Ubuntu", "Microsoft JhengHei", "sans-serif"])
    app.setFont(default_font)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
