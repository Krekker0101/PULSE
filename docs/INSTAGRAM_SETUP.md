# Instagram: настройка владельцем

1. Используйте профессиональный Instagram-аккаунт Business или Creator. PULSE использует Instagram Login; Facebook Page для этого пути не требуется.
2. Откройте [Meta Developer Apps](https://developers.facebook.com/apps/), создайте приложение с Instagram API и включите настройку Instagram Login. Названия пунктов консоли могут меняться; проверяйте [официальный раздел](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/).
3. В настройках OAuth зарегистрируйте точный HTTPS redirect URI: `https://ВАШ-ДОМЕН/oauth/instagram/callback`. Домен направьте на существующий VPS и OAuth bridge через HTTPS reverse proxy.
4. В защищённом `backend/.env` на сервере задайте `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, `PUBLIC_BASE_URL`, `ENCRYPTION_KEY`. Используйте Instagram App ID/Secret выбранного продукта; не передавайте секреты в Telegram.
5. Запрашиваемые разрешения: `instagram_business_basic`, `instagram_business_content_publish`. В режиме разработки назначьте роли и тестовые аккаунты в консоли Meta и примите приглашения. Для пользователей вне ролей выполните необходимые требования App Review/Advanced Access, проверки бизнеса и публикации приложения.
6. Запустите профиль `social-oauth`, затем в Telegram: «Мои аккаунты» → Instagram → авторизация → «Проверить доступ» → включить публикации.
7. Отправьте собственный MP4 боту, подтвердите права в карточке источника, выберите направления и подтвердите отправку. Контейнер ещё не означает публикацию: PULSE ожидает обработку, вызывает media_publish и проверяет возвращённый media ID.

Instagram должен иметь доступ к подписанному HTTPS URL видео до окончания обработки. `/media/` нельзя блокировать общим паролем reverse proxy. Доступ ограничен сроком ссылки и HMAC; access log должен быть выключен для callback и подписанных ссылок.

Токены шифруются; обновление выполняется по сроку действия. При отключении локальные credentials удаляются. Дополнительно отзовите приложение в настройках Instagram. При смене ключа шифрования сохраните старый ключ в `ENCRYPTION_PREVIOUS_KEYS` на период переподключения/перешифрования.

Источники: [официальная коллекция Meta](https://www.postman.com/meta/instagram/folder/1z5vxzu/instagram-api-with-instagram-login), [Content Publishing](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing). Прямые страницы Meta были недоступны инструменту чтения в этой сессии; live-контракт должен быть проверен на тестовом профессиональном аккаунте перед production.
