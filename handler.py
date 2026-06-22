"""RunPod Serverless handler: audio (base64) + lyrics -> karaoke word timings.

Input  (job["input"]):
  audio     : base64-encoded mp3/wav/m4a  (required)
  lyrics    : song text, one line per karaoke line  (required)
  language  : ISO code, default "ru"
  isolate   : bool, run Demucs vocal isolation first (default true)
  filename  : optional original name (for extension hint)

Output:
  {
    "lines":  [ {s, e, words:[{t,s,e}, ...]}, ... ],   # word timings
    "lrc":    "[mm:ss.xxx] line\\n...",                  # ready LRC text
    "method": "demucs+stable-ts",
    "stats":  {lines, words, lyric_words, device}
  }
"""
from __future__ import annotations

import base64
import binascii
import os
import tempfile
import traceback
from pathlib import Path

import runpod

from karaoke_align import align_lyrics, pick_device, to_json, to_lrc

_VALID_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}


def _decode_audio(b64: str, filename: str | None) -> Path:
    if "," in b64[:64] and b64.lstrip().startswith("data:"):
        b64 = b64.split(",", 1)[1]  # strip data:audio/...;base64, prefix
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"audio is not valid base64: {e}")
    if len(raw) < 1000:
        raise ValueError("audio too small / empty after decode")

    ext = Path(filename or "").suffix.lower()
    if ext not in _VALID_EXT:
        ext = ".mp3"
    tmp = Path(tempfile.mkdtemp(prefix="kr_")) / f"track{ext}"
    tmp.write_bytes(raw)
    return tmp


def handler(job):
    try:
        inp = job.get("input") or {}
        audio_b64 = inp.get("audio")
        lyrics = (inp.get("lyrics") or "").strip()

        if not audio_b64:
            return {"error": "missing 'audio' (base64-encoded track)"}
        if not lyrics:
            return {"error": "missing 'lyrics' (song text)"}

        language = inp.get("language", "ru")
        isolate = bool(inp.get("isolate", True))
        title = inp.get("title", "")
        artist = inp.get("artist", "")

        track = _decode_audio(audio_b64, inp.get("filename"))

        lines = align_lyrics(
            track, lyrics, language=language, isolate=isolate,
            work_dir="/tmp/karaoke",
        )

        if not lines:
            return {"error": "alignment produced no lines — check audio/lyrics match"}

        lines_json = to_json(lines)
        word_count = sum(len(l["words"]) for l in lines_json)
        lyric_words = len([w for ln in lyrics.splitlines() for w in ln.split() if w.strip()])

        return {
            "lines": lines_json,
            "lrc": to_lrc(lines, title=title, artist=artist),
            "method": "demucs+stable-ts" if isolate else "stable-ts",
            "stats": {
                "lines": len(lines_json),
                "words": word_count,
                "lyric_words": lyric_words,
                "device": pick_device(),
            },
        }
    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()[-1500:]}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
