#!/usr/bin/env bash
# Авто-удаление караоке-страниц старше TTL.
# Удаляет ТОЛЬКО папки с меткой .karaoke (созданные сборщиком).
# Чужое (health/, plans/, hello.html, .git ...) не трогается.
set -euo pipefail

cd "$(dirname "$0")"
# подхватываем KARAOKE_DIR и KARAOKE_TTL_HOURS из .env
[ -f .env ] && set -a && . ./.env && set +a

DIR="${KARAOKE_DIR:-/var/www/claude-test}"
TTL_HOURS="${KARAOKE_TTL_HOURS:-48}"
MIN=$((TTL_HOURS * 60))

# метка .karaoke лежит в DIR/<slug>/.karaoke → удаляем родительскую папку
find "$DIR" -mindepth 2 -maxdepth 2 -name ".karaoke" -mmin "+$MIN" -printf '%h\0' 2>/dev/null \
  | xargs -0 -r rm -rf
