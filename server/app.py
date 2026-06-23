"""Karaoke-сборщик (FastAPI) для VDS.

Поток: загрузка mp3 + текста -> RunPod (demucs+align) -> сборка караоке-страницы
-> сохранение в KARAOKE_DIR/<slug>/ -> ссылка на готовую страницу.

RunPod считает (GPU, эфемерно), VDS хранит и раздаёт (nginx).

Переменные окружения:
  RUNPOD_API_KEY      ключ RunPod (Settings -> API Keys)
  RUNPOD_ENDPOINT_ID  ID serverless-эндпоинта
  KARAOKE_DIR         куда складывать страницы (по умолч. /var/www/karaoke)
  PUBLIC_BASE         публичный адрес (напр. https://karaoke.nonstopplay.ru)
"""
from __future__ import annotations

import os
import re
import time
import unicodedata

import requests
from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from karaoke_html import build_html

RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", "")
KARAOKE_DIR = os.environ.get("KARAOKE_DIR", "/var/www/karaoke")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")

app = FastAPI(title="Karaoke сборщик")


def _slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    s = "".join(translit.get(ch, ch) for ch in s.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s or "track")[:48]


def _call_runpod(job_input: dict) -> dict:
    if not RUNPOD_API_KEY or not RUNPOD_ENDPOINT_ID:
        raise RuntimeError("RUNPOD_API_KEY / RUNPOD_ENDPOINT_ID не заданы в окружении")
    base = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}"
    headers = {"Authorization": f"Bearer {RUNPOD_API_KEY}"}
    r = requests.post(f"{base}/run", json={"input": job_input}, headers=headers, timeout=120)
    r.raise_for_status()
    job_id = r.json().get("id")
    if not job_id:
        raise RuntimeError(f"RunPod не вернул id задачи: {r.text[:300]}")

    deadline = time.time() + 600  # до 10 минут (с учётом cold start)
    while time.time() < deadline:
        time.sleep(3)
        s = requests.get(f"{base}/status/{job_id}", headers=headers, timeout=60)
        s.raise_for_status()
        st = s.json()
        state = st.get("status")
        if state == "COMPLETED":
            out = st.get("output") or {}
            if "error" in out:
                raise RuntimeError(f"RunPod: {out['error']}")
            return out
        if state in ("FAILED", "CANCELLED", "TIMED_OUT"):
            raise RuntimeError(f"RunPod задача {state}: {str(st)[:300]}")
    raise RuntimeError("RunPod: превышено время ожидания")


FORM = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Сделать караоке</title>
<style>
 body{font-family:-apple-system,Segoe UI,sans-serif;background:#0d0d14;color:#fff;
   max-width:620px;margin:0 auto;padding:32px 18px}
 h1{font-size:1.4rem} label{display:block;margin:18px 0 6px;opacity:.8}
 input[type=file],textarea{width:100%;padding:10px;border-radius:8px;border:1px solid #333;
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
 <input type=text name=title style="width:100%;padding:10px;border-radius:8px;border:1px solid #333;background:#1a1a24;color:#fff">
 <button>Собрать караоке</button>
 <p class=hint>Обработка на GPU — обычно меньше минуты, при «холодном старте» до пары минут.</p>
</form></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return FORM


@app.get("/health")
def health():
    return {"ok": True, "endpoint_set": bool(RUNPOD_ENDPOINT_ID), "dir": KARAOKE_DIR}


@app.post("/make")
async def make(audio: UploadFile, lyrics: str = Form(...), title: str = Form("")):
    raw = await audio.read()
    if len(raw) < 1000:
        return JSONResponse({"error": "пустой или слишком маленький файл"}, status_code=400)
    if not lyrics.strip():
        return JSONResponse({"error": "пустой текст"}, status_code=400)

    title = (title or os.path.splitext(audio.filename or "track")[0]).strip()

    # 1) сохраняем аудио в папку страницы — оно сразу публично раздаётся nginx
    slug = f"{_slugify(title)}-{int(time.time()) % 100000}"
    page_dir = os.path.join(KARAOKE_DIR, slug)
    os.makedirs(page_dir, exist_ok=True)
    audio_path = os.path.join(page_dir, "audio.mp3")
    with open(audio_path, "wb") as f:
        f.write(raw)

    if not PUBLIC_BASE:
        return JSONResponse({"error": "PUBLIC_BASE не задан — RunPod не сможет скачать аудио"},
                            status_code=500)
    audio_url = f"{PUBLIC_BASE}/{slug}/audio.mp3"

    # 2) RunPod скачивает аудио по ссылке (маленький payload) и считает тайминги
    try:
        out = _call_runpod({
            "audio_url": audio_url, "lyrics": lyrics, "language": "ru",
            "isolate": True, "filename": "audio.mp3", "title": title,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    lines = out.get("lines") or []
    if not lines:
        return JSONResponse({"error": "RunPod вернул пустые тайминги"}, status_code=502)

    # 3) собираем index.html рядом с уже лежащим audio.mp3
    html_str = build_html(lines, "audio.mp3", title)
    with open(os.path.join(page_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_str)

    if out.get("lrc"):
        with open(os.path.join(page_dir, f"{slug}.lrc"), "w", encoding="utf-8") as f:
            f.write(out["lrc"])

    url = f"{PUBLIC_BASE}/{slug}/" if PUBLIC_BASE else f"/{slug}/"
    body = (f"<html><head><meta charset=utf-8><meta http-equiv=refresh content='1;url={url}'>"
            f"</head><body style='font-family:sans-serif;background:#0d0d14;color:#fff;padding:40px'>"
            f"✅ Готово! Открываю <a style='color:#a78bfa' href='{url}'>{url}</a> …"
            f"<br><br>{out.get('stats', {})}</body></html>")
    return HTMLResponse(body)
