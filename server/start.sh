#!/usr/bin/env bash
# Запуск/перезапуск сборщика (форма) и Telegram-бота. Вызывается вручную и по @reboot.
cd ~/karaoke-runpod/server
set -a; . ./.env; set +a

# веб-форма
pkill -f "uvicorn app:app" 2>/dev/null; sleep 1
setsid ./.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8800 \
    </dev/null >>~/karaoke-runpod/server/uvicorn.log 2>&1 &

# Telegram-бот (если задан токен)
if grep -q '^BOT_TOKEN=..' .env 2>/dev/null; then
    pkill -f "[.]venv/bin/python bot.py" 2>/dev/null; sleep 1
    setsid ./.venv/bin/python bot.py \
        </dev/null >>~/karaoke-runpod/server/bot.log 2>&1 &
fi
