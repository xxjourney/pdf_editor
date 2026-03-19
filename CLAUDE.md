# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands use the project's virtualenv at `venv/`.

```bash
# Run the FastAPI web app
GEMINI_API_KEY=<key> venv/bin/uvicorn main:app --reload

# Run the native Qt desktop app
venv/bin/python pdf_editor_qt.py

# Diagnose Gemini API key
GEMINI_API_KEY=<key> venv/bin/python check_gemini.py

# Install dependencies
venv/bin/pip install -r requirements.txt
```

## Architecture

This repo contains **two independent PDF editors** that share no code:

### 1. Web app (`main.py` + `static/index.html`)
- **Backend**: FastAPI serving `static/index.html` as a SPA. Only two API routes:
  - `POST /api/translate` — calls Gemini via `google-genai` SDK (`google.genai.Client`), async
  - `POST /api/export` — receives raw PDF bytes + highlights JSON + watermark string, applies them with PyMuPDF, returns modified PDF as a download
- **Frontend**: Single-file vanilla JS in `static/index.html`. Uses PDF.js (CDN) to render pages and manage a text/annotation layer. Highlight coordinates are stored in JS state and POSTed to `/api/export` at save time.
- **Gemini client**: Initialised once at module level as `_gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])`. The env var must be set before starting the server.

### 2. Native Qt desktop app (`pdf_editor_qt.py`)
- Self-contained PyQt6 application, no server required.
- **Key classes**:
  - `PageView(QWidget)` — renders a PDF page via PyMuPDF into a QPixmap (HiDPI-aware using `devicePixelRatioF`). Handles word-level text selection (hit-test against `page.get_text("words")`) and watermark overlay (drag to move/resize/rotate via `QTransform`).
  - `MainWindow(QMainWindow)` — owns the document, highlights list, watermarks list, and all sidebar state. Coordinates between `PageView` signals and sidebar widgets.
  - `TranslateDialog(QDialog)` — calls `translate()` synchronously on open; calls `QApplication.processEvents()` first so "Translating…" is visible.
  - `translate()` — free function supporting Gemini, DeepL, and Google Translate (REST). Uses `google-genai` SDK (`google.genai.Client`) for Gemini, **not** the old `google-generativeai` SDK.

### Watermark system (Qt app)

Multiple watermarks are supported. Each watermark is a `dict`:

```python
{
    "type":         "text" | "image",
    "text":         str,           # text content (type=text)
    "image_path":   str,           # original file path (type=image)
    "image_pixmap": QPixmap|None,  # processed pixmap for display
    "image_bytes":  bytes|None,    # PNG bytes for PDF export
    "x_pct":        float,         # anchor X as fraction of page width
    "y_pct":        float,         # anchor Y as fraction of page height
    "fontsize":     int,           # pt (text size, or display height for image)
    "angle":        int,           # CCW degrees
    "visible":      bool,          # toggled via checkbox in list
}
```

**Rendering order** (PageView.paintEvent):
1. PDF page pixmap (SourceOver)
2. Watermarks — text uses `CompositionMode_Multiply` so dark ink is unaffected; image uses SourceOver (transparency baked in)
3. Highlights / selection
4. Active-watermark bounding box + drag handles (SourceOver, always on top)

**Image processing** (`_process_watermark_image`):
- Requires Pillow (`pip install Pillow`)
- Background removal: uses `rembg` if installed AND `USE_REMBG = True` (top of file), otherwise threshold-based (lightness > 200 → transparent)
- Opacity: alpha channel scaled to 35%
- Stores both `QPixmap` (display) and PNG bytes (export)

**Export** (`_export_pdf`):
- Text: `TextWriter` + `write_text(..., opacity=0.35, overlay=True)`
- Image: PIL pre-rotates for arbitrary angles, then `page.insert_image(..., overlay=True)`
- `overlay=True` is required — `overlay=False` is hidden behind the PDF's own white background fill

**`USE_REMBG` flag** (line ~23): set `False` to skip rembg even when installed; uses threshold-based fallback instead.

### Coordinate systems
- PyMuPDF uses PDF points (72 dpi). Zoom factor maps points ↔ screen pixels: `screen_px = pdf_pt × zoom`.
- `PageView` stores highlights as `fitz.Rect` (PDF points). Watermark position is stored as `(x_pct, y_pct)` — fractions of page width/height — so it's resolution-independent.
- Watermark rotation: PyMuPDF uses CCW-positive (`fitz.Matrix(angle)`); Qt painter uses CW-positive, so the overlay uses `painter.rotate(-angle)`.

### Translation providers (Qt app)
Configured via sidebar dropdowns. `GEMINI_MODELS` lists only models confirmed available for the current API key tier — update this list if a model returns 404. The `google-genai` SDK (`from google import genai`) is required; the old `google-generativeai` package will not work.

### CJK font requirement
The Qt app requires a CJK-capable font to display Chinese translation results. On WSL/Linux without system CJK fonts, symlink Windows fonts:
```bash
mkdir -p ~/.local/share/fonts/windows-cjk
ln -sf /mnt/c/Windows/Fonts/msjh.ttc ~/.local/share/fonts/windows-cjk/
fc-cache -f ~/.local/share/fonts/windows-cjk
```
