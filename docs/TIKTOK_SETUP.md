# TikTok: настройка владельцем

1. Создайте приложение в [TikTok Developer Portal](https://developers.tiktok.com/), добавьте Login Kit и Content Posting API для загрузки видео на завершение публикации пользователем.
2. Зарегистрируйте Web redirect URI `https://ВАШ-ДОМЕН/oauth/tiktok/callback`, добавьте требуемые платформой сведения приложения, privacy policy/terms и демонстрацию сценария для проверки.
3. В защищённом серверном env задайте `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `PUBLIC_BASE_URL`, `ENCRYPTION_KEY`. Не отправляйте ключи и OAuth tokens в чат.
4. Разрешения PULSE: `user.info.basic`, `video.upload`; `video.list` запрашивается только при `TIKTOK_DISCOVERY_ENABLED=true`. PULSE сохраняет scopes, фактически предоставленные TikTok, включая отказ в отдельных scopes.
5. Для PULL_FROM_URL подтвердите владение доменом или префиксом `PUBLIC_BASE_URL` в консоли TikTok. Только после успешной проверки выставьте `TIKTOK_VERIFIED_MEDIA_DOMAIN=true`. Не включайте этот флаг по предположению.
6. Включите профиль `social-oauth`, затем авторизуйтесь из «Мои аккаунты» → TikTok, проверьте доступ и включите публикации.
7. Отправьте разрешённый собственный файл боту и подтвердите передачу в выбранный аккаунт. `DRAFT_CREATED` означает передачу в inbox TikTok; завершите публикацию в самом TikTok. PULSE не считает черновик опубликованным видео.
8. Если API не разрешён, используйте «Получить в Telegram» в карточке видео, описание исходного файла и ручную публикацию. YouTube-ссылка остаётся ссылкой; PULSE не скачивает её в обход правил.

**Direct Post отключён для этого проекта.** [Правила TikTok](https://developers.tiktok.com/docs/en/content-sharing-guidelines) запрещают Direct Post для частной утилиты управления своими/командными аккаунтами и копирования произвольного контента других платформ. Наличие `video.publish` не доказывает разрешённость приложения. Изменение этого режима требует допустимого продукта, аудита и полного обязательного UX: актуальная Creator Info, явная приватность без значения по умолчанию, опции взаимодействия, проверка длительности, согласие на конкретную публикацию.

Web OAuth использует server-side client secret; [PKCE](https://developers.tiktok.com/docs/en/oauth-user-access-token-management) требуется в указанном TikTok контракте для mobile/desktop, поэтому к Web OAuth выдуманный PKCE не добавлен. Refresh token заменяется новым при ротации. Отзыв доступа выполняйте также в настройках подключённых приложений TikTok.

Документация: [Upload](https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload), [token management](https://developers.tiktok.com/docs/en/oauth-user-access-token-management), [posting status](https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status), [domain verification](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide).
