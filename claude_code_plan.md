# PDF Editor Implementation Plan for Claude Code

This document provides a step-by-step implementation plan designed to be executed by Claude Code. 

**Instructions for User:** Feed the prompts under each "Execution Step" to Claude Code sequentially. Wait for Claude to finish implementing and testing each step before proceeding to the next.

---

## Prerequisites
Before starting, ensure you are in the target directory for the project: `/home/brianhsu/pdf_editor`

## Execution Step 1: Project Initialization and Backend Setup

**Prompt to give Claude:**
```text
Please initialize a new Python-based web application for a PDF Editor in the current directory. 
Requirements:
1. Use FastAPI as the web framework.
2. Set up a virtual environment (`python -m venv venv`) and activate it (or create instructions for me to activate it).
3. Create a `requirements.txt` containing at least `fastapi`, `uvicorn`, `PyMuPDF` (for PDF manipulation), and `jinja2` (if needed for templating, though static files might be enough).
4. Create a basic `main.py` that serves a static HTML page (`index.html`) from a `static/` directory.
5. Create an empty `static/index.html` file.
6. Provide the command to run the development server.
```

## Execution Step 2: Frontend Setup with PDF.js

**Prompt to give Claude:**
```text
Now, let's set up the frontend to view PDFs.
1. Download or include `PDF.js` (Mozilla's library) in the `static/` directory (you can use a CDN link in the HTML for simplicity initially).
2. Update `static/index.html` to include a canvas element for rendering the PDF.
3. Add a file upload input (`<input type="file">`) to allow the user to select a local PDF file.
4. Write JavaScript in `static/index.html` (or a linked `script.js` file) to use `PDF.js` to load the selected file, render the first page onto the canvas, and add basic "Next Page" / "Previous Page" buttons.
5. Ensure the FastAPI backend has a route to handle serving these static files correctly.
```

## Execution Step 3: Implement Text Selection and Highlighting UI

**Prompt to give Claude:**
```text
Let's add the ability to select text and highlight it (fluorescent pen) on the frontend.
1. Enable the `PDF.js` text layer so users can select text over the rendered canvas.
2. Add an event listener for text selection (e.g., `mouseup` after selecting).
3. When text is selected, show a small floating button or context menu near the selection with an option to "Highlight".
4. When "Highlight" is clicked, capture the coordinates of the selected text.
5. Draw a semi-transparent yellow rectangle (the highlight) over those coordinates on a separate "annotation layer" canvas overlaid on top of the PDF canvas.
6. Store these highlight coordinates in a JavaScript array/state object so we can send them to the backend later.
```

## Execution Step 4: Implement Text Translation UI and Backend Stub

**Prompt to give Claude:**
```text
Now, let's implement the translation feature.
1. Add a "Translate" option to the text selection context menu we created in the previous step.
2. When "Translate" is clicked, extract the actual text string that was selected.
3. Create a new FastAPI endpoint `POST /api/translate` in `main.py` that accepts a JSON payload with `{"text": "..."}`.
4. For now, just have the backend return a mock translation (e.g., return "TRANSLATED: " + original_text). We'll add a real API later.
5. In the frontend JavaScript, make a `fetch` call to this `/api/translate` endpoint when the "Translate" button is clicked.
6. Display the returned translated text in a small popup or tooltip near the selected text.
```

## Execution Step 5: Implement Watermarking UI

**Prompt to give Claude:**
```text
Let's add the UI for configuring a watermark.
1. Add a section in the UI (e.g., a sidebar or modal) for "Watermark Settings".
2. Include an input field for the watermark text.
3. Add a button "Apply Watermark".
4. Store this watermark text in the frontend state. We won't apply it visually in the browser yet; it will be applied on the backend during export.
```

## Execution Step 6: Backend PDF Modification (Export)

**Prompt to give Claude:**
```text
This is the core backend logic step.
1. Create a "Save/Export PDF" button in the frontend.
2. When clicked, the frontend should gather:
   - The original PDF file (either re-upload it or refer to a temporarily saved one on the server).
   - The array of highlight coordinates.
   - The watermark text.
3. Send this data to a new FastAPI endpoint `POST /api/export`.
4. In `main.py`, implement the `/api/export` endpoint. Use `PyMuPDF` (fitz) to:
   - Open the PDF.
   - Iterate through the pages and apply the watermark text (diagonally across the page, low opacity).
   - Iterate through the highlight coordinates and draw standard PDF highlight annotations at those locations.
5. Save the modified PDF to a temporary file or byte stream.
6. Return the modified PDF as a file download response to the frontend, so the user's browser prompts them to save the new `.pdf` file.
```

## Execution Step 7: Refinement and Real Translation (Optional)

**Prompt to give Claude:**
```text
Review the code for any edge cases.
1. Ensure coordinate mapping between the `PDF.js` canvas (which might be scaled) and the backend `PyMuPDF` coordinate system (usually points, 72 dpi) is accurate for the highlights. This is a common issue.
2. If you have a preferred translation API (like Google Cloud Translation or DeepL), please replace the mock translation in `POST /api/translate` with actual API calls. (Make sure to ask me for API keys if needed, don't hardcode them).
3. Add basic CSS styling to make the application look clean and modern.
```