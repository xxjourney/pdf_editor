# PDF Editor

A PDF editor with two independent implementations: a **FastAPI web app** and a **native PyQt6 desktop app**.

## Features

### Native Qt Desktop App (`pdf_editor_qt.py`)
- Open and navigate multi-page PDFs
- **Page navigation**: toolbar SpinBox for direct page jump; mouse wheel flips pages at scroll boundaries (Ctrl+wheel to zoom)
- Word-level text selection with highlight and translate actions
- **Multiple watermarks** — text or image, each independently configurable
- Watermark drag-to-reposition, resize handle, rotate handle on canvas
- Export PDF with progress bar (cancelable) and completion dialog
- Dark theme UI

#### Watermark features
- Add/delete multiple watermarks via sidebar list
- Per-watermark **visibility checkbox** and **opacity control** (10–100%, default 35%)
- **Text watermarks**: configurable text, font size, position, angle
- **Image watermarks**: load PNG/JPG, automatic background removal; opacity controlled at paint/export time (not baked into stored bytes)
- Watermarks rendered using Multiply blend so dark text is never obscured
- Exported with per-watermark `opacity` via PyMuPDF

#### Translation
- Providers: Gemini, DeepL, Google Translate, Mock (offline)
- Select text on page → right-click → Translate

### Web App (`main.py`)
- Browser-based PDF viewer built on PDF.js
- Text highlight and export via FastAPI backend
- Single watermark (text), applied server-side with PyMuPDF
- Translation via Gemini API

## Requirements

```
Python 3.11+
Pillow          # required for image watermarks
Pillow[numpy]   # recommended for fast background removal
rembg           # optional: AI background removal (better quality)
PyQt6
PyMuPDF
fastapi, uvicorn[standard]   # web app only
google-genai, deepl, requests  # translation providers
```

Install all dependencies:
```bash
venv/bin/pip install -r requirements.txt
```

## Usage

### Desktop app
```bash
venv/bin/python pdf_editor_qt.py
```

### Web app
```bash
GEMINI_API_KEY=<key> venv/bin/uvicorn main:app --reload
# Open http://localhost:8000
```

## Configuration

### `USE_REMBG` flag (`pdf_editor_qt.py` line ~23)
```python
USE_REMBG = True   # use rembg AI background removal (requires: pip install rembg)
USE_REMBG = False  # use threshold-based fallback (no extra dependency)
```

### CJK fonts (WSL/Linux)
Required to display Chinese translation results:
```bash
mkdir -p ~/.local/share/fonts/windows-cjk
ln -sf /mnt/c/Windows/Fonts/msjh.ttc ~/.local/share/fonts/windows-cjk/
fc-cache -f ~/.local/share/fonts/windows-cjk
```

### Gemini models
`GEMINI_MODELS` list in `pdf_editor_qt.py` — update if a model returns 404 for your API key tier.
