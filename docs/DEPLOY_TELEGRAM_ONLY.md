# PULSE — удалённый Telegram-only запуск

**Предварительное условие:** всегда работающий Linux VPS или облачный Background Worker с постоянным процессом, исходящим HTTPS (Telegram и YouTube API) и доступом к Neon. Обычный веб-сайт на бесплатном сервисе с автоматическим засыпанием **не обеспечивает** постоянный мониторинг.

## Один раз: личные секреты (не присылайте в ChatGPT)

- `BOT_TOKEN`: откройте [@BotFather](https://t.me/BotFather), создайте бот или возьмите текущий токен.
- `OWNER_TELEGRAM_ID`: числовой Telegram ID владельца, можно узнать у [@userinfobot](https://t.me/userinfobot).
- `YOUTUBE_API_KEY`: в [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com) включите **YouTube Data API v3**. Затем в [Credentials](https://console.cloud.google.com/apis/credentials) создайте API key, ограничьте его YouTube Data API v3 и доступом только с IP сервера, если возможно. Убедитесь в доступной квоте.
- `DATABASE_URL`: [Neon Console](https://console.neon.tech) → ваш проект `pulse` → **Connect** → строка PostgreSQL. Схема PULSE требует `postgresql+psycopg://...` и `sslmode=require`. Учётные данные скрывайте.
- `ENCRYPTION_KEY`: запустите на сервере разовую команду `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` либо используйте надёжный генератор секретов вашего хостинга. Не меняйте ключ после добавления OAuth-токенов без процедуры ротации.
- **Upstash Redis здесь необязателен.** Состояния Telegram FSM находятся в PostgreSQL; ошибка DNS Upstash не останавливает бот.

### Инструкция для Linux VPS

```
# После безопасной передачи папки PULSE на ваш удалённый Linux VPS:
cd PULSE
cp .env.telegram.example backend/.env
# Отредактируйте backend/.env непосредственно на сервере и установите chmod 600 backend/.env
chmod 600 backend/.env
docker compose -f docker-compose.telegram.yml up --build -d
docker compose -f docker-compose.telegram.yml logs -f --tail=100 pulse
```

Если Docker Compose недоступен, можно использовать фонового Python worker на поддерживаемом облачном сервисе с командой запуска из `docker-compose.telegram.yml`. Запускайте **ровно один** экземпляр polling-бота с данным токеном: два экземпляра будут конфликтовать за Telegram getUpdates.

Первоначальная установка выполнит Alembic `upgrade head` в Neon. Ничего не удаляйте из старых таблиц: миграции аддитивные. Для обновления — резервная копия БД, обновление кода, перезапуск сервиса. Все повседневные настройки затем задаются через бот. Историю видео хранит Neon, а не пользовательский компьютер.

### Как пользоваться

В Telegram открыть бот → `/start` → **▶️ YouTube / Источники** → **➕ Канал / Shorts / видео** → отправить ссылку. Перейти в карточку источника, выбрать SHORT/LONG/ALL и пороги, затем **🔄 Сканировать**. Если ключ YouTube отсутствует или исчерпана квота, бот сообщит о реальной ошибке, а не выдумает ролики.

Для получения именно MP4: самостоятельно загрузить **свой или лицензированный** файл как Telegram видео в личный чат с ботом. Это создаёт Telegram `file_id`. Без наличия исходного файла или отдельного разрешённого механизма передачи YouTube-ссылку невозможно автоматически превратить в пригодный для Instagram/TikTok файл через официальный YouTube Data API.

### OAuth Instagram / TikTok

Базовая установка не содержит публичных HTTP-портов и не создаёт веб-страницу. Подключение новых социальных аккаунтов возможно **только** если дополнительно настроить официальные OAuth-приложения, публичный HTTPS-домен и опциональный `oauth-bridge` из Compose (`--profile social-oauth`). Прокси должен маршрутизировать HTTPS к `127.0.0.1:18080`; остальные пути, кроме OAuth callbacks и подписанной раздачи заранее разрешённых медиа, не нужны. При авторизации пользователь всё равно должен открыть официальный сайт Instagram/TikTok: без этого безопасный OAuth невозможен. Бот остаётся единственным интерфейсом повседневного управления.

Не включайте `oauth-bridge`, если вам достаточно YouTube-мониторинга. Наличие модуля не является доказательством разрешённого Direct Post. Ограничения описаны в `docs/ENGINEERING_AUDIT_TELEGRAM.md`.

### Обслуживание и безопасность

- Не публикуйте `backend/.env`, пароли Neon, Google API key или Bot Token.
- Используйте `OWNER_TELEGRAM_ID` строго числовым; бот игнорирует команды других пользователей.
- Для Telegram-only *нет* публичной панели API, так что ошибкам `GET /api/... 401` неоткуда появиться.
- Убедитесь, что VPS разрешает исходящий DNS/HTTPS; отключение сети или Neon остановит работу. Включите резервное копирование Neon на самостоятельный зашифрованный носитель.
- Для полной остановки: `docker compose -f docker-compose.telegram.yml down`. Не используйте `down -v` без понимания удаления томов.
