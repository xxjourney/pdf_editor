import io
import json
import os
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types

app = FastAPI(title="PDF Editor")

app.mount("/static", StaticFiles(directory="static"), name="static")

_gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/api/translate")
async def translate(payload: dict):
    text = payload.get("text", "").strip()
    target_lang = payload.get("target_lang", "Traditional Chinese")

    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    prompt = (
        f"Translate the following text to {target_lang}. "
        f"Return only the translation, no explanation:\n\n{text}"
    )

    response = await _gemini.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.2),
    )
    return JSONResponse({"translated": response.text.strip()})


@app.post("/api/export")
async def export_pdf(
    file: UploadFile = File(...),
    highlights: str = Form("[]"),
    watermark: str = Form(""),
):
    pdf_bytes = await file.read()
    highlight_list = json.loads(highlights)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for page in doc:
        # Apply diagonal watermark
        if watermark:
            page_rect = page.rect
            page.insert_text(
                fitz.Point(page_rect.width / 2, page_rect.height / 2),
                watermark,
                fontsize=60,
                color=(0.75, 0.75, 0.75),
                rotate=45,
                overlay=True,
            )

        # Apply highlights for this page
        page_num = page.number
        for h in highlight_list:
            if h.get("page") != page_num:
                continue
            # h contains: page, x, y, width, height (in PDF points)
            rect = fitz.Rect(h["x"], h["y"], h["x"] + h["width"], h["y"] + h["height"])
            annot = page.add_highlight_annot(rect)
            annot.update()

    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=edited.pdf"},
    )
