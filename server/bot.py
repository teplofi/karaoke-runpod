"""Telegram-бот караоке: mp3 + текст -> ссылка на караоке-страницу.

Лимит BOT_DAILY_LIMIT (по умолч. 5) треков в день на пользователя, бесплатно.
Логика обработки — в core.process_karaoke (общая с веб-формой).
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from datetime import date

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import core

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DAILY_LIMIT = int(os.environ.get("BOT_DAILY_LIMIT", "5"))
MAX_AUDIO = 20 * 1024 * 1024  # лимит Telegram на скачивание ботом
USAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_usage.json")

WELCOME = (
    "🎤 *Караоке-бот*\n\n"
    "Пришли *mp3-трек*, затем *текст песни* (каждая строка — отдельная строка караоке) — "
    "и я верну ссылку на интерактивную страницу с подсветкой слов в ритм музыки.\n\n"
    f"Бесплатно, до *{DAILY_LIMIT} треков в день*.\n\n"
    "Поехали — отправь аудиофайл 🎵"
)


def _load() -> dict:
    try:
        with open(USAGE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: dict) -> None:
    tmp = USAGE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, USAGE_FILE)


def _used(uid: int) -> int:
    return int(_load().get(date.today().isoformat(), {}).get(str(uid), 0))


def _incr(uid: int) -> None:
    today = date.today().isoformat()
    d = _load()
    d = {today: d.get(today, {})}  # храним только сегодняшний день
    d[today][str(uid)] = int(d[today].get(str(uid), 0)) + 1
    _save(d)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    left = max(0, DAILY_LIMIT - _used(update.effective_user.id))
    await update.message.reply_text(WELCOME + f"\n\nОсталось сегодня: *{left}*",
                                    parse_mode="Markdown")


async def on_audio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    obj = msg.audio or msg.document
    if not obj:
        return
    if (obj.file_size or 0) > MAX_AUDIO:
        await msg.reply_text("Файл больше 20 МБ — Telegram не даёт скачать его боту. "
                             "Пришли трек полегче (mp3 2–6 МБ).")
        return
    name = getattr(obj, "file_name", None) or ((getattr(obj, "title", None) or "track") + ".mp3")
    await msg.reply_chat_action(ChatAction.TYPING)
    try:
        f = await obj.get_file()
        data = bytes(await f.download_as_bytearray())
    except Exception as e:
        await msg.reply_text(f"Не смог скачать файл: {e}")
        return
    ctx.user_data["audio"] = data
    ctx.user_data["audio_name"] = name
    await msg.reply_text("Принял трек 🎵 Теперь пришли *текст песни* "
                         "(каждая строка — отдельной строкой).", parse_mode="Markdown")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    data = ctx.user_data.get("audio")
    if not data:
        await msg.reply_text("Сначала пришли mp3-трек 🎵, потом текст песни.")
        return
    uid = update.effective_user.id
    if _used(uid) >= DAILY_LIMIT:
        await msg.reply_text(f"На сегодня лимит исчерпан ({DAILY_LIMIT} трека). "
                             "Возвращайся завтра 🙂")
        return

    name = ctx.user_data.get("audio_name", "track.mp3")
    title = os.path.splitext(name)[0]
    wait = await msg.reply_text("⏳ Делаю караоке… обычно меньше минуты "
                                "(при «холодном старте» — до пары минут).")
    try:
        res = await asyncio.to_thread(core.process_karaoke, data, name, msg.text or "", title)
    except ValueError as e:
        await wait.edit_text(f"⚠️ {e}")
        return
    except Exception as e:
        await wait.edit_text(f"❌ Ошибка обработки: {e}")
        return

    _incr(uid)
    ctx.user_data.pop("audio", None)
    ctx.user_data.pop("audio_name", None)
    left = max(0, DAILY_LIMIT - _used(uid))
    st = res.get("stats", {})
    await wait.edit_text(
        f"✅ Готово!\n\n🎤 {res['url']}\n\n"
        f"Слов размечено: {st.get('words', '?')}/{st.get('lyric_words', '?')}\n"
        f"Осталось сегодня: {left}"
    )
    if res.get("lrc"):
        bio = io.BytesIO(res["lrc"].encode("utf-8"))
        bio.name = f"{res['slug']}.lrc"
        try:
            await msg.reply_document(bio, caption="Файл синхронизации (.lrc)")
        except Exception:
            pass


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")
    app = (Application.builder().token(BOT_TOKEN)
           .connect_timeout(30).read_timeout(30)
           .get_updates_connect_timeout(30).get_updates_read_timeout(45)
           .build())
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, on_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    print("[BOT] запущен, polling…")
    # bootstrap_retries=-1 — бесконечно повторять при сетевых таймаутах на старте
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1,
                    drop_pending_updates=True)


if __name__ == "__main__":
    main()
