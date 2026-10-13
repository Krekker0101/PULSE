# Развёртывание расширения на существующем сервере

Не создавайте новую БД и не заменяйте существующий `backend/.env`. Сохраните Neon DATABASE_URL, owner ID, bot token, YouTube key и encryption key. Новые значения смотрите в `.env.telegram.example`.

1. Сохраните backup существующей БД. С установленным PostgreSQL client и серверным env выполните `python scripts/backup_database.py --directory /secure/pulse-backups`. Дампы содержат данные и ciphertext; храните их в закрытом хранилище, с ограниченными правами и периодически проверяйте восстановление в отдельную БД. Используйте прямую строку `DATABASE_URL_UNPOOLED` без `-pooler`.
2. На отдельной Neon branch проверьте `cd backend && python -m alembic upgrade head`, количество users/sources/accounts/jobs и сохранность истории. Повторите upgrade, затем примените обновление на существующем сервере. Не запускайте несколько экземпляров long polling с одним token.
3. Основной сервис: `docker compose -f docker-compose.telegram.yml up --build -d pulse`. Он выполняет миграции и запускает Telegram runtime. Новые OAuth-подключения по умолчанию имеют выключенные публикации; существующие строки и credentials сохраняются.
4. Для социальных платформ настройте ключи и HTTPS domain по INSTAGRAM_SETUP.md/TIKTOK_SETUP.md. Включите bridge: `docker compose -f docker-compose.telegram.yml --profile social-oauth up --build -d`. Bridge доступен на loopback 127.0.0.1:18080; HTTPS reverse proxy должен проксировать /oauth/, /media/, /clips/, /healthz. Управляющей веб-панели нет.
5. Не включайте access logs с OAuth query или signed media URLs; uvicorn access log уже отключён. Настройте reverse proxy аналогично. Healthz проверяет БД, container health — heartbeat и БД. Docker restart policy восстанавливает завершившийся процесс; статус unhealthy требует внешнего мониторинга, Docker сам по нему не перезапускает контейнер.
6. Контейнер имеет memory/CPU и log limits; медиа ограничены MAX_STORAGE_BYTES и очищаются после MEDIA_RETENTION_HOURS, если нет незавершённой публикации. Нужны FFmpeg/ffprobe для social export. Защита quota не заменяет ограничение размера filesystem тома.
7. SIGTERM/SIGINT отменяют polling, monitor, worker и heartbeat с закрытием сессий. Вызов, завершившийся после прекращения процесса, сверяется через состояние очереди; Telegram uncertainty автоматически не повторяется.
8. Проверьте acceptance: owner-only callback, OAuth replay/expiry/permissions, свой профессиональный Instagram, TikTok draft, публичный/приватный канал, группа, тема, потеря прав, multi-destination, рестарт во время отправки. Конкретную тестовую публикацию подтверждайте в Telegram.

Ротация encryption key: задайте новый ENCRYPTION_KEY, оставьте старые в ENCRYPTION_PREVIOUS_KEYS, обновите процессы и переподключите/обновите credentials. Удаляйте старый ключ только когда все оставшиеся ciphertext включая Telegram session перешифрованы или более не нужны. Backup ключей храните отдельно от БД.

Локальная проверка: `python -m pytest backend/tests -q`. В этой поставке migration tests используют SQLite; PostgreSQL locking и серверная HTTPS конфигурация требуют проверки на isolated branch/server.
