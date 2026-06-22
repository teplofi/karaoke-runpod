# Karaoke-сборщик на VDS

Связывает **RunPod (GPU-вычисления)** и **VDS (хранение + раздача)**.
Льёшь mp3 + текст → RunPod считает тайминги → сборщик собирает караоке-страницу
→ сохраняет в `/var/www/karaoke/<slug>/` → отдаёт ссылку. nginx раздаёт страницы статикой.

```
форма (/) ─▶ FastAPI /make ─▶ RunPod (demucs+align) ─▶ собрать html ─▶ /var/www/karaoke/<slug>/
                                                                          │
                                                  nginx ◀── karaoke.nonstopplay.ru/<slug>/
```

## Файлы
| Файл | Что |
|---|---|
| `app.py` | FastAPI: форма загрузки + `/make` (вызов RunPod, сборка страницы) |
| `karaoke_html.py` | сборка караоке-HTML из таймингов |
| `requirements.txt` | fastapi, uvicorn, requests |
| `karaoke.service` | systemd-юнит |
| `nginx-karaoke.conf` | конфиг nginx для поддомена |
| `.env.example` | шаблон ключей (скопировать в `.env`) |

## Деплой на VDS (Ubuntu, пользователь teplofi)

> Предполагается, что RunPod-эндпоинт уже создан и есть **API-ключ** + **Endpoint ID**.
> Поддомен `karaoke.nonstopplay.ru` должен указывать A-записью на IP сервера.

```bash
# 1. Код на сервер
sudo git clone https://github.com/teplofi/karaoke-runpod.git /opt/karaoke-runpod
cd /opt/karaoke-runpod/server

# 2. Окружение
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt

# 3. Ключи
sudo cp .env.example .env
sudo nano .env          # впиши RUNPOD_API_KEY и RUNPOD_ENDPOINT_ID

# 4. Папка под страницы
sudo mkdir -p /var/www/karaoke
sudo chown -R www-data:www-data /var/www/karaoke /opt/karaoke-runpod

# 5. systemd-сервис
sudo cp karaoke.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now karaoke
sudo systemctl status karaoke --no-pager     # должно быть active (running)
curl -s localhost:8800/health                # {"ok":true,...}

# 6. nginx + SSL
sudo cp nginx-karaoke.conf /etc/nginx/sites-available/karaoke.nonstopplay.ru
sudo ln -s /etc/nginx/sites-available/karaoke.nonstopplay.ru /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d karaoke.nonstopplay.ru
```

Готово: открой `https://karaoke.nonstopplay.ru` → загрузи трек и текст → получишь ссылку
на интерактивную караоke-страницу.

## Обновление кода
```bash
cd /opt/karaoke-runpod && sudo git pull && sudo systemctl restart karaoke
```

## Заметки
- **Диск.** Каждая страница ≈ размер mp3 (~5 МБ). Следи за местом: `df -h /`.
  При нехватке — чистить старые `/var/www/karaoke/*` или выносить аудио в S3/R2.
- **Лимит загрузки** — `client_max_body_size 30m` в nginx (хватает на треки 2–6 мин).
- **Долгий первый запрос** — cold start RunPod; `proxy_read_timeout 700s` это покрывает.
