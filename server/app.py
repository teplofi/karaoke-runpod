"""Karaoke-сборщик (FastAPI): веб-форма -> core.process_karaoke.

Форма под basic-auth (nginx). Логика обработки — в core.py (общая с ботом).
"""
from __future__ import annotations

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

import core

app = FastAPI(title="Karaoke сборщик")

FORM = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Сделать караоке</title>
<style>
 body{font-family:-apple-system,Segoe UI,sans-serif;background:#0d0d14;color:#fff;
   max-width:620px;margin:0 auto;padding:32px 18px}
 h1{font-size:1.4rem} label{display:block;margin:18px 0 6px;opacity:.8}
 input[type=file],textarea,input[type=text]{width:100%;padding:10px;border-radius:8px;border:1px solid #333;
   background:#1a1a24;color:#fff;font-size:1rem} textarea{min-height:220px;resize:vertical}
 button{margin-top:20px;width:100%;padding:14px;border:0;border-radius:10px;cursor:pointer;
   background:linear-gradient(90deg,#a78bfa,#f0abfc);color:#1a1a2e;font-size:1.05rem;font-weight:700}
 .hint{opacity:.5;font-size:.85rem;margin-top:6px}
</style></head><body>
<h1>🎤 Сделать караоке</h1>
<form method=post action=/make enctype=multipart/form-data>
 <label>MP3-трек</label>
 <input type=file name=audio accept=".mp3,.wav,.m4a" required>
 <label>Текст песни <span class=hint>(одна строка = одна строка караоке)</span></label>
 <textarea name=lyrics required placeholder="Строка 1&#10;Строка 2&#10;..."></textarea>
 <label>Название <span class=hint>(необязательно)</span></label>
 <input type=text name=title>
 <button>Собрать караоке</button>
 <p class=hint>Обработка на GPU — обычно меньше минуты, при «холодном старте» до пары минут.</p>
</form></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return FORM


@app.get("/health")
def health():
    return {"ok": True, "endpoint_set": bool(core.RUNPOD_ENDPOINT_ID), "dir": core.KARAOKE_DIR}


@app.post("/make")
async def make(audio: UploadFile, lyrics: str = Form(...), title: str = Form("")):
    raw = await audio.read()
    try:
        res = core.process_karaoke(raw, audio.filename or "track.mp3", lyrics, title)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    url = res["url"]
    body = (f"<html><head><meta charset=utf-8><meta http-equiv=refresh content='1;url={url}'>"
            f"</head><body style='font-family:sans-serif;background:#0d0d14;color:#fff;padding:40px'>"
            f"✅ Готово! Открываю <a style='color:#a78bfa' href='{url}'>{url}</a> …"
            f"<br><br>{res.get('stats', {})}</body></html>")
    return HTMLResponse(body)
