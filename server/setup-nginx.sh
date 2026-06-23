#!/usr/bin/env bash
# Поднимает форму караоке на nonstopplay.ru под basic-auth.
# Запуск с root (sudo). Логин и пароль basic-auth — аргументами:
#   sudo bash setup-nginx.sh <логин> '<пароль>'
# Делает бэкап текущего конфига и откатывается, если nginx -t упадёт.
set -euo pipefail

CONF=/etc/nginx/sites-available/claude-test
HT=/etc/nginx/.htpasswd_karaoke
AUTH_USER="${1:-neiroslop}"
AUTH_PASS="${2:?Укажи пароль вторым аргументом: sudo bash setup-nginx.sh neiroslop 'ПАРОЛЬ'}"

[ -f "$CONF" ] || { echo "Не найден конфиг $CONF"; exit 1; }

# 1) бэкап
cp -a "$CONF" "$CONF.bak"
echo "Бэкап конфига: $CONF.bak"

# 2) файл паролей basic-auth (apr1-хэш через openssl, пароль идёт по stdin)
HASH=$(openssl passwd -apr1 -stdin <<<"$AUTH_PASS")
printf '%s:%s\n' "$AUTH_USER" "$HASH" > "$HT"
chmod 640 "$HT"; chown root:www-data "$HT" 2>/dev/null || true
echo "Создан $HT (логин: $AUTH_USER)"

# 3) новый конфиг: форма и /make -> сборщик (127.0.0.1:8800), страницы -> статикой
cat > "$CONF" <<'NGINX'
server {
    listen 80; listen [::]:80;
    server_name nonstopplay.ru www.nonstopplay.ru;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl; listen [::]:443 ssl;
    http2 on;
    server_name nonstopplay.ru www.nonstopplay.ru;

    ssl_certificate /etc/letsencrypt/live/nonstopplay.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/nonstopplay.ru/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    auth_basic "Karaoke";
    auth_basic_user_file /etc/nginx/.htpasswd_karaoke;

    client_max_body_size 30m;
    root /var/www/claude-test;
    index index.html;

    location = /     { proxy_pass http://127.0.0.1:8800; proxy_set_header Host $host; proxy_read_timeout 700s; }
    location = /make { proxy_pass http://127.0.0.1:8800; proxy_set_header Host $host; proxy_read_timeout 700s; }
    # audio.mp3 без авторизации — чтобы RunPod мог скачать трек для обработки
    location ~* /audio\.mp3$ { auth_basic off; try_files $uri =404; }
    location /       { try_files $uri $uri/ =404; }
}
NGINX

# 4) проверка и перезагрузка (или откат)
if nginx -t 2>/tmp/ngt.log; then
    systemctl reload nginx
    echo "OK: nginx перезагружен."
    echo "Форма: https://nonstopplay.ru  (логин: $AUTH_USER)"
else
    echo "ОШИБКА nginx -t — откатываю:"; cat /tmp/ngt.log
    cp -a "$CONF.bak" "$CONF"
    echo "Конфиг восстановлен из бэкапа, nginx не тронут."
    exit 1
fi
