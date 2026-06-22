# 🎤 Karaoke on RunPod Serverless

Выравнивание караоке (тайминги каждого слова) на GPU за центы.
На вход — **mp3 + текст песни**, на выход — **тайминги слов (JSON) + готовый `.lrc`**.

Внутри та же логика, что локально: **Demucs** отделяет вокал → **stable-ts (Whisper small)**
делает forced-alignment текста под аудио. На сервере всё крутится на **CUDA (GPU)**.

```
audio (base64) + lyrics  ─▶  Demucs (вокал)  ─▶  Whisper align  ─▶  {lines, lrc}
```

---

## Что внутри

| Файл | Зачем |
|---|---|
| `karaoke_align.py` | ядро: Demucs + forced-align + экспорт JSON/LRC |
| `handler.py` | точка входа RunPod (принимает запрос, возвращает тайминги) |
| `Dockerfile` | образ с CUDA-torch + demucs + whisper (модели **запечены** в образ) |
| `requirements.txt` | зависимости |
| `call_runpod.py` | вызов готового эндпоинта с твоего компа |
| `test_local.py` | локальная проверка handler без RunPod (на Mac — через MPS) |

---

## Деплой на RunPod (через GitHub — без Docker Hub)

> Так RunPod сам соберёт образ из этого репозитория. Ничего собирать/пушить руками не надо.

### 1. Залить проект на GitHub
Уже сделано на этом компе (`git`-репозиторий готов). Осталось запушить:
```bash
cd ~/Desktop/вайб/karaoke-runpod
# создай пустой репозиторий на github.com (например karaoke-runpod), потом:
git remote add origin git@github.com:ТВОЙ_ЛОГИН/karaoke-runpod.git
git push -u origin main
```

### 2. Создать Serverless-эндпоинт
1. Зайди на **runpod.io → Serverless → New Endpoint**
2. Источник: **GitHub Repo** → подключи свой GitHub → выбери репозиторий `karaoke-runpod`
   (RunPod увидит `Dockerfile` в корне и соберёт образ сам — первый билд ~10–20 мин,
   т.к. качаются torch и модели)
3. **GPU**: выбери **16 GB (T4 / RTX A4000)** — самый дешёвый, для этой задачи хватает
4. **Workers**: `Active = 0`, `Max = 1` (scale-to-zero — платишь только за обработку)
5. **Container Disk**: 15–20 GB (образ с моделями весит ~8–10 GB)
6. **Idle Timeout**: 5 секунд (быстрее засыпает = меньше платишь)
7. Создай эндпоинт. Дождись, пока статус билда станет **готов**.

### 3. Взять ключи
- **API-ключ**: RunPod → *Settings → API Keys* → создать (роль: read/write)
- **Endpoint ID**: на странице эндпоинта (вид `abc123xyz`)

### 4. Проверить вызовом
В RunPod на странице эндпоинта есть вкладка **Requests** — можно отправить тестовый JSON прямо там:
```json
{ "input": { "audio": "<base64-аудио>", "lyrics": "строка1\nстрока2", "language": "ru" } }
```
Или с компа скриптом (удобнее):
```bash
export RUNPOD_API_KEY=ключ
export RUNPOD_ENDPOINT_ID=айди
python call_runpod.py track.mp3 lyrics.txt
# → рядом появится track.lrc
```

---

## Формат запроса/ответа

**Запрос** (`input`):
| поле | тип | по умолчанию | что это |
|---|---|---|---|
| `audio` | base64 | — | mp3/wav/m4a, закодированный в base64 |
| `lyrics` | строка | — | текст: **одна строка = одна строка караоке** |
| `language` | строка | `"ru"` | язык |
| `isolate` | bool | `true` | отделять вокал через Demucs (точнее на треках с битом) |
| `title`/`artist` | строка | `""` | для тегов в `.lrc` |

**Ответ**:
```json
{
  "lines": [ { "s": 14.2, "e": 16.1, "words": [ {"t":"Все","s":14.2,"e":14.5}, ... ] } ],
  "lrc": "[00:14.200] Все они яман\n...",
  "method": "demucs+stable-ts",
  "stats": { "lines": 24, "words": 110, "lyric_words": 112, "device": "cuda" }
}
```

Из `lines` (тайминги слов) на твоей стороне можно собрать и `.lrc`, и интерактивную
HTML-страницу караоке (как в проекте «шортсы», функция `generate_karaoke_page`).

---

## Локальная проверка (до деплоя)

На Mac можно прогнать handler без RunPod — пойдёт через Apple GPU (MPS):
```bash
cd ~/Desktop/вайб/karaoke-runpod
pip install -r requirements.txt        # + torch/torchaudio для Mac
python test_local.py track.mp3 lyrics.txt
```
> На Mac модели те же, но медленнее, чем на серверном GPU. Это только для проверки логики.

---

## Экономика

GPU-время ~30–60 с на трек (вместе с cold start). На **T4** это ≈ **$0.005–0.01 за трек**.

| Треков/мес | ≈ стоимость |
|---|---|
| 100 | ~$1 |
| 1 000 | ~$8–12 |
| 10 000 | ~$80–120 |

Твои **$10** на балансе RunPod ≈ **1000–2000 треков** для старта.
В простое (никто не шлёт запросы) — **$0**, потому что `Active workers = 0`.

---

## Возможные проблемы

- **Билд падает на torch/torchaudio** — версии в `Dockerfile`/`requirements.txt`; базовый образ
  `pytorch:2.4.0-cuda12.1-cudnn9`. Если RunPod сменит доступные CUDA — подправить тег базового образа.
- **`cuDNN`/`libcudnn` ошибка у faster-whisper** — образ уже на `cudnn9`; если всплывёт,
  значит версия CTranslate2 хочет другой cudnn — понизить `faster-whisper`.
- **Долгий первый запрос** — это cold start (поднятие воркера). Модели запечены в образ,
  так что повторные запросы заметно быстрее. Можно поднять `Idle Timeout`, чтобы воркер
  дольше не засыпал между треками (но это чуть дороже).
- **Большой трек не лезет в base64-лимит** — для длинных файлов лучше слать через storage (S3/R2)
  и передавать URL; для песен 2–5 мин base64 нормально.
