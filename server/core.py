"""Общее ядро: трек + текст -> RunPod -> караоке-страница на VDS.

Используют и веб-форма (app.py), и Telegram-бот (bot.py).
"""
from __future__ import annotations

import os
import re
import time
import unicodedata

import requests

from karaoke_html import build_html

RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", "")
KARAOKE_DIR = os.environ.get("KARAOKE_DIR", "/var/www/claude-test")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s or "track")[:48]


def _call_runpod(job_input: dict) -> dict:
    if not RUNPOD_API_KEY or not RUNPOD_ENDPOINT_ID:
        raise RuntimeError("RUNPOD_API_KEY / RUNPOD_ENDPOINT_ID не заданы")
    base = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}"
    headers = {"Authorization": f"Bearer {RUNPOD_API_KEY}"}
    r = requests.post(f"{base}/run", json={"input": job_input}, headers=headers, timeout=120)
    r.raise_for_status()
    job_id = r.json().get("id")
    if not job_id:
        raise RuntimeError(f"RunPod не вернул id: {r.text[:300]}")
    deadline = time.time() + 600
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
            raise RuntimeError(f"RunPod задача {state}")
    raise RuntimeError("RunPod: превышено время ожидания")


def process_karaoke(audio_bytes: bytes, filename: str, lyrics: str, title: str = "") -> dict:
    """mp3-байты + текст -> сохранить страницу на VDS, вернуть {url, slug, lrc, stats}."""
    lyrics = (lyrics or "").strip()
    if not audio_bytes or len(audio_bytes) < 1000:
        raise ValueError("пустой или слишком маленький аудиофайл")
    if not lyrics:
        raise ValueError("пустой текст песни")
    if not PUBLIC_BASE:
        raise RuntimeError("PUBLIC_BASE не задан — RunPod не сможет скачать аудио")

    title = (title or os.path.splitext(filename or "track")[0]).strip()
    slug = f"{slugify(title)}-{int(time.time()) % 100000}"
    page_dir = os.path.join(KARAOKE_DIR, slug)
    os.makedirs(page_dir, exist_ok=True)

    with open(os.path.join(page_dir, "audio.mp3"), "wb") as f:
        f.write(audio_bytes)
    audio_url = f"{PUBLIC_BASE}/{slug}/audio.mp3"

    out = _call_runpod({
        "audio_url": audio_url, "lyrics": lyrics, "language": "ru",
        "isolate": True, "filename": "audio.mp3", "title": title,
    })
    lines = out.get("lines") or []
    if not lines:
        raise RuntimeError("RunPod вернул пустые тайминги — проверь, что текст совпадает с треком")

    with open(os.path.join(page_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_html(lines, "audio.mp3", title))
    if out.get("lrc"):
        with open(os.path.join(page_dir, f"{slug}.lrc"), "w", encoding="utf-8") as f:
            f.write(out["lrc"])
    # метка для авто-чистки по TTL
    with open(os.path.join(page_dir, ".karaoke"), "w") as f:
        f.write(str(int(time.time())))

    return {
        "url": f"{PUBLIC_BASE}/{slug}/" if PUBLIC_BASE else f"/{slug}/",
        "slug": slug,
        "lrc": out.get("lrc", ""),
        "stats": out.get("stats", {}),
    }
