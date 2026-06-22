#!/usr/bin/env python3
"""Вызов задеплоенного RunPod-эндпоинта с локального компа.

Перед запуском задай переменные окружения:
  export RUNPOD_API_KEY=...      # API-ключ из RunPod (Settings → API Keys)
  export RUNPOD_ENDPOINT_ID=...  # ID эндпоинта (на странице Serverless endpoint)

Запуск:
  python call_runpod.py path/to/track.mp3 path/to/lyrics.txt

Шлёт трек (base64) + текст, ждёт результат, сохраняет track.lrc рядом.
"""
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


def _post(url, payload, api_key):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    if len(sys.argv) < 3:
        print("usage: python call_runpod.py <track.mp3> <lyrics.txt>")
        sys.exit(1)

    api_key = os.environ.get("RUNPOD_API_KEY")
    endpoint = os.environ.get("RUNPOD_ENDPOINT_ID")
    if not api_key or not endpoint:
        print("Задай RUNPOD_API_KEY и RUNPOD_ENDPOINT_ID в окружении.")
        sys.exit(1)

    track = Path(sys.argv[1])
    lyrics = Path(sys.argv[2]).read_text(encoding="utf-8")
    audio_b64 = base64.b64encode(track.read_bytes()).decode()

    base = f"https://api.runpod.ai/v2/{endpoint}"
    payload = {"input": {
        "audio": audio_b64, "lyrics": lyrics, "language": "ru",
        "isolate": True, "filename": track.name, "title": track.stem,
    }}

    print(f"→ отправляю {track.name} на эндпоинт {endpoint}…")
    started = _post(f"{base}/run", payload, api_key)
    job_id = started.get("id")
    if not job_id:
        print("Не удалось запустить задачу:", started)
        sys.exit(1)

    # poll
    while True:
        time.sleep(3)
        req = urllib.request.Request(
            f"{base}/status/{job_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            status = json.loads(r.read())
        state = status.get("status")
        print("  статус:", state)
        if state == "COMPLETED":
            out = status.get("output", {})
            break
        if state in ("FAILED", "CANCELLED", "TIMED_OUT"):
            print("Задача не выполнена:", json.dumps(status, ensure_ascii=False)[:800])
            sys.exit(1)

    if "error" in out:
        print("ОШИБКА на сервере:", out["error"])
        sys.exit(1)

    print("stats:", json.dumps(out.get("stats", {}), ensure_ascii=False))
    lrc_path = track.with_suffix(".lrc")
    lrc_path.write_text(out["lrc"], encoding="utf-8")
    print("LRC сохранён:", lrc_path)


if __name__ == "__main__":
    main()
