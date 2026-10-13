# Первое подключение Instagram и TikTok

В текущей конфигурации ключи приложений отсутствуют, а callback указывает на локальный HTTP. Кнопка входа требует зарегистрированного приложения платформы и публичного HTTPS callback. Telegram Bot Token и YouTube API key их не заменяют.

## 1. Публичный HTTPS callback

Для постоянной работы разместите PULSE на вашем постоянно работающем VPS. Домен или поддомен, например `pulse.ваш-домен`, должен вести на этот сервер. Настройте TLS-сертификат и reverse proxy для OAuth bridge. `PUBLIC_BASE_URL` — адрес вида `https://pulse.ваш-домен`, без `/oauth` и без параметров.

При Docker-развёртывании bridge включается командой из корня:

```sh
docker compose -f docker-compose.telegram.yml --profile social-oauth up --build -d
```

HTTPS reverse proxy должен направлять `/oauth/`, `/media/`, `/clips/`, `/healthz` на `127.0.0.1:18080`. При локальном запуске из `pulse_local.py start` callback доступен через существующий API на `127.0.0.1:8000`; для доступа платформ нужен настроенный постоянный HTTPS tunnel или сервер. Простая замена `http` на `https` в env не создаёт доступный callback.

Проверьте через внешнюю сеть `https://ВАШ-ДОМЕН/healthz` для bridge или `/health/ready` для локального API. Доступ к callback не должен требовать пароля reverse proxy. Отключите access logs с OAuth codes и подписанными media URLs. Включение домена, сертификата и маршрутизации выполняется отдельно; команда oauth-setup не разворачивает инфраструктуру.

## 2. Instagram

1. Откройте [Meta Developer Apps](https://developers.facebook.com/apps/), зарегистрируйте developer-профиль и создайте приложение с Instagram API.
2. Выберите настройку **Instagram API with Instagram Login**. Используйте Instagram App ID и Instagram App Secret из настроек этого продукта.
3. Добавьте OAuth redirect URI точно: `https://ВАШ-ДОМЕН/oauth/instagram/callback`.
4. Используйте профессиональный Instagram Business/Creator. Добавьте нужный аккаунт в роли/тестеры приложения и примите приглашение, если этого требует режим разработки.
5. PULSE запрашивает `instagram_business_basic` и `instagram_business_content_publish`. Для доступа вне ролей приложения выполните необходимые условия проверки и Advanced Access в консоли Meta. Не включайте лишние permissions.

Названия пунктов консоли могут меняться. [Официальный раздел Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/). Прямая страница Meta вернула 429 инструменту чтения; шаги в конкретной консоли и live-контракт требуют проверки владельцем.

## 3. TikTok

1. Откройте [TikTok for Developers](https://developers.tiktok.com/) → Manage apps → зарегистрируйте приложение.
2. Добавьте **Login Kit**, платформу **Web**, redirect URI: `https://ВАШ-ДОМЕН/oauth/tiktok/callback`. Адрес должен быть статическим HTTPS URL, без query и fragment.
3. Добавьте **Content Posting API**, сценарий загрузки на завершение публикации пользователем. Получите одобрение `video.upload`; `user.info.basic` используется для идентификации.
4. Скопируйте **Client key** и **Client secret** в локальную команду настройки. Не отправляйте их боту или в этот чат.
5. Перед передачей видео по URL подтвердите владение доменом/префиксом в TikTok. Только после этого задайте `TIKTOK_VERIFIED_MEDIA_DOMAIN=true` в серверном env.
6. Черновик доставляется в TikTok inbox; публикацию завершает пользователь. Для private owner-only PULSE Direct Post остаётся отключённым.

[Официальная инструкция Login Kit Web](https://developers.tiktok.com/docs/en/login-kit-web), [Content Posting API](https://developers.tiktok.com/docs/content-posting-api-get-started-upload-content).

## 4. Сохранение настроек без передачи секретов в чат

После получения домена и ключей из корня проекта:

```powershell
cd D:\PROJECTS\PULSE
python scripts\pulse_local.py oauth-setup
```

Выберите Instagram, TikTok или оба. Введите реальный HTTPS base URL и ключи. Скрытый ввод ключей не отображает даже звёздочки — это нормально. Команда сохраняет изменяемые настройки атомарно в `backend/.env`, сохраняя БД, bot token, существующий ключ шифрования и остальные значения. Она проверяет формат конфигурации, но не проверяет подлинность ключей и одобрение платформы.

Остановите прежний PULSE через Ctrl+C и снова выполните:

```powershell
python scripts\pulse_local.py start
```

В Telegram: «Мои аккаунты» → Instagram/TikTok → официальный вход → «Проверить доступ» → включить публикации. При отсутствии настроек бот теперь покажет точные недостающие переменные; при ошибке БД он сообщит о БД отдельно.
