#!/usr/bin/env python3
"""Локальная проверка handler перед деплоем (без RunPod).

Запуск:
  python test_local.py path/to/track.mp3 path/to/lyrics.txt

Кодирует трек в base64, вызывает handler как RunPod, печатает статистику
и сохраняет track.lrc рядом. На Mac пойдёт через MPS, на сервере — через CUDA.
"""
import base64
import json
import sys
from pathlib import Path

from handler import handler


def main():
    if len(sys.argv) < 3:
        print("usage: python test_local.py <track.mp3> <lyrics.txt>")
        sys.exit(1)

    track = Path(sys.argv[1])
    lyrics_file = Path(sys.argv[2])
    lyrics = lyrics_file.read_text(encoding="utf-8")
    audio_b64 = base64.b64encode(track.read_bytes()).decode()

    job = {"input": {
        "audio": audio_b64,
        "lyrics": lyrics,
        "language": "ru",
        "isolate": True,
        "filename": track.name,
        "title": track.stem,
    }}

    print(f"→ handler: {track.name} ({len(audio_b64)//1024} KB base64), "
          f"{len(lyrics.splitlines())} строк текста")
    out = handler(job)

    if "error" in out:
        print("ОШИБКА:", out["error"])
        if out.get("trace"):
            print(out["trace"])
        sys.exit(1)

    print("method:", out["method"], "| stats:", json.dumps(out["stats"], ensure_ascii=False))
    lrc_path = track.with_suffix(".lrc")
    lrc_path.write_text(out["lrc"], encoding="utf-8")
    print("LRC сохранён:", lrc_path)
    print("\n--- первые строки LRC ---")
    print("\n".join(out["lrc"].splitlines()[:8]))


if __name__ == "__main__":
    main()
