# Белградская бродилка — Telegram Mini App

## Запуск
```
pip install -r requirements.txt
cp .env.example .env   # вписать BOT_TOKEN
export $(cat .env | xargs) && uvicorn app:app --host 0.0.0.0 --port 8000
```
Без `BOT_TOKEN` работает тестовый вход по имени (вне Telegram).

## Подключение к Telegram
Mini App требует HTTPS-адрес (nginx + сертификат или туннель вроде cloudflared).
1. @BotFather → `/newbot` → токен в `.env`.
2. @BotFather → `/newapp` (или Bot Settings → Menu Button) → указать HTTPS-URL сайта.
3. Участники открывают бота и жмут кнопку меню — вход автоматический, регистрация не нужна.
Первый вошедший становится организатором.
