# IPTV Archive Proxy

Прокси-сервер для подключения IPTV-провайдеров с форматом архива **Shift** к плеерам, использующим формат **Archive** (например, Vision).

## Проблема

Провайдер ([tvtm.one](http://tvtm.one)) хранит токен авторизации в query-параметре URL:
```
http://ru7.tvtm.one/ch001/index.m3u8?token=user.v2_XXX...
```

Плеер Vision при архивном запросе убирает все query-параметры, оставляя только путь. Токен теряется, архив не работает.

У каждого канала свой уникальный токен — вынести в конфиг нельзя.

## Решение

Прокси встраивает токен в **path** URL, откуда плеер его не может удалить:

```
Оригинал:  http://ru7.tvtm.one/ch001/index.m3u8?token=user.v2_XXX
Прокси:    http://proxy/stream/ru7.tvtm.one/user.v2_XXX/ch001/index.m3u8
```

При архивном запросе Vision добавляет `?archive=X&archive_end=Y` к этому URL — токен остаётся в пути, прокси его извлекает и пересылает провайдеру.

## Схема работы

```
Плеер (Vision/Chillio)           Прокси                      Провайдер
     │                              │                              │
     │ GET /playlist.m3u8           │                              │
     │─────────────────────────────>│ GET playlist от провайдера  │
     │                              │─────────────────────────────>│
     │                              │<─────────────────────────────│
     │<─────────────────────────────│ URL переписаны (токен в пути)│
     │                              │ + catchup-атрибуты (опц.)   │
     │                              │                              │
     │ GET /stream/ru7.../TOKEN/ch001/index.m3u8?archive=X&archive_end=Y
     │─────────────────────────────>│                              │
     │                              │ GET /ch001/mono.m3u8         │
     │                              │   ?token=TOKEN&utc=X&lutc=Y  │
     │                              │─────────────────────────────>│
     │                              │ 301 → mono-X-DUR.m3u8       │
     │                              │<─────────────────────────────│
     │<─────────────────────────────│ M3U с абс. URL сегментов    │
     │                              │                              │
     │      Скачивает .ts сегменты напрямую у провайдера           │
```

## Конвертация параметров

| Vision (плеер)  | Провайдер (Shift) | Значение              |
|-----------------|-------------------|-----------------------|
| `archive=X`     | `utc=X`           | UNIX timestamp начала |
| `archive_end=Y` | `lutc=Y`          | UNIX timestamp конца  |
| токен в path    | `token=TOKEN`     | Возвращается в query  |
| `index.m3u8`    | `mono.m3u8`       | Эндпоинт архива       |

## Быстрый старт

### 1. Настройка

Отредактируйте `docker-compose.yml`:

```yaml
environment:
  PLAYLIST_URL: "http://tvtm.one/pl/3/TOKEN1/playlist.m3u8"  # URL плейлиста провайдера
  PROXY_HOST: "192.168.1.100"  # IP вашей машины в локальной сети
  PROXY_PORT: "8080"           # порт (опционально, по умолчанию 8080)
  CACHE_TTL: "300"             # кэш плейлиста в секундах (опционально)
  CATCHUP_DAYS: "7"            # глубина архива в днях для Chillio (0 = отключено)
```

### 2. Запуск

```bash
docker compose up -d
```

### 3. Настройка плеера

В плеере (Vision / Chillio) заменить URL плейлиста:

```
Было:  http://tvtm.one/pl/3/TOKEN1/playlist.m3u8
Стало: http://192.168.1.100:8080/playlist.m3u8
```

## Переменные окружения

| Переменная     | Обязательная | По умолчанию | Описание                                               |
|----------------|:------------:|:------------:|--------------------------------------------------------|
| `PLAYLIST_URL` | ✓            | —            | Полный URL плейлиста провайдера                        |
| `PROXY_HOST`   | ✓            | —            | IP/хост прокси (виден плееру)                          |
| `PROXY_PORT`   |              | `8080`       | Порт прокси                                            |
| `CACHE_TTL`    |              | `300`        | Время кэша плейлиста (секунды)                         |
| `CATCHUP_DAYS` |              | `0`          | Глубина архива в днях; `0` — catchup-теги не добавляются |

### Catchup (Chillio)

При `CATCHUP_DAYS > 0` в каждый `#EXTINF` добавляются атрибуты для плеера Chillio:

```
#EXTINF:-1 tvg-id="ch001" group-title="News" catchup="default" catchup-days="7" catchup-source="http://proxy/stream/ru7.tvtm.one/TOKEN/ch001/index.m3u8?archive={utc}&archive_end={lutc}",Channel 1
http://proxy/stream/ru7.tvtm.one/TOKEN/ch001/index.m3u8
```

Chillio подставляет UNIX-временны́е метки в `{utc}` / `{lutc}` — прокси пересылает их провайдеру как `utc` / `lutc`.

## API

| Маршрут | Описание |
|---------|----------|
| `GET /playlist.m3u8` | Переписанный плейлист провайдера |
| `GET /stream/{host}/{token}/{path}` | Прямой эфир |
| `GET /stream/{host}/{token}/{path}?archive=X&archive_end=Y` | Архив |

## Верификация

```bash
# Плейлист переписан корректно (без catchup-тегов при CATCHUP_DAYS=0)
curl http://localhost:8080/playlist.m3u8

# Плейлист с catchup-атрибутами (CATCHUP_DAYS=7)
# → #EXTINF содержит catchup="default" catchup-days="7" catchup-source="...?archive={utc}&archive_end={lutc}"
curl http://localhost:8080/playlist.m3u8

# Прямой эфир
curl "http://localhost:8080/stream/ru7.tvtm.one/TOKEN/ch001/index.m3u8"

# Архив (запрос, который генерирует Chillio после подстановки меток)
curl "http://localhost:8080/stream/ru7.tvtm.one/TOKEN/ch001/index.m3u8?archive=1771536600&archive_end=1771539300"
```

## Стек

- [FastAPI](https://fastapi.tiangolo.com/) — веб-фреймворк
- [httpx](https://www.python-httpx.org/) — async HTTP-клиент с поддержкой редиректов
- [uvicorn](https://www.uvicorn.org/) — ASGI-сервер
- Docker / Docker Compose
