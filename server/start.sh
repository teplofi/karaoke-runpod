#!/usr/bin/env bash
# Запуск/перезапуск сборщика (форма) и Telegram-бота. Вызывается вручную и по @reboot.
cd ~/karaoke-runpod/server
set -a; . ./.env; set +a

# веб-форма
pkill -f "uvicorn app:app" 2>/dev/null; sleep 1
setsid ./.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8800 \
    </dev/null >>~/karaoke-runpod/server/uvicorn.log 2>&1 &

# Telegram-бот (если задан токен) — в авто-рестарт-цикле на случай падений
if grep -q '^BOT_TOKEN=..' .env 2>/dev/null; then
    pkill -f "bot_loop" 2>/dev/null
    pkill -f "[.]venv/bin/python bot.py" 2>/dev/null; sleep 1
    setsid bash -c '
        # bot_loop
        cd ~/karaoke-runpod/server
        set -a; . ./.env; set +a
        while true; do
            ./.venv/bin/python bot.py
            echo "[BOT] упал, перезапуск через 5с $(date)"
            sleep 5
        done' </dev/null >>~/karaoke-runpod/server/bot.log 2>&1 &
fi
