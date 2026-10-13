# ⚡ PULSE — Smart Social Media Automation

🇷🇺 **Русский**

**PULSE** — интеллектуальная система автоматизации социальных сетей, полностью управляемая через Telegram-бота.

Платформа позволяет отслеживать YouTube-каналы, Shorts и видео, анализировать статистику просмотров, лайков и комментариев, находить популярный контент, настраивать фильтры и управлять публикациями в Instagram, TikTok и Telegram.

Возможности:
- 📊 Мониторинг YouTube и анализ популярности видео.
- 🎯 Гибкая фильтрация контента по статистике, длительности и дате публикации.
- 🔗 Подключение собственных аккаунтов Instagram, TikTok и Telegram.
- 🚀 Управление публикациями, очередями и расписаниями.
- 🤖 Полностью интерактивный интерфейс внутри Telegram.
- ☁️ Круглосуточная работа на удалённом сервере.
- 🔐 Безопасное управление доступом и интеграциями.
- 🗄️ PostgreSQL (Neon), Python, Docker и YouTube Data API v3.

**PULSE объединяет мониторинг контента и управление социальными сетями в одном Telegram-боте.** Загрузка и публикация видео выполняются только при наличии разрешённого медиаконтента, необходимых прав и доступа к API платформ.

---

🇬🇧 **English**

**PULSE** is an intelligent social media automation system managed entirely through a Telegram bot.

The platform monitors YouTube channels, Shorts, and videos, analyzes views, likes, and comments, discovers trending content, applies customizable filters, and manages publishing workflows across Instagram, TikTok, and Telegram.

Key features:
- 📊 YouTube monitoring and video performance analysis.
- 🎯 Advanced filtering by engagement, duration, and publication date.
- 🔗 Integration with your Instagram, TikTok, and Telegram accounts.
- 🚀 Publishing queues, scheduling, and destination management.
- 🤖 Fully interactive Telegram-based interface.
- ☁️ 24/7 operation on a remote server.
- 🔐 Secure access control and account integrations.
- 🗄️ Powered by Python, PostgreSQL (Neon), Docker, and YouTube Data API v3.

**PULSE brings content discovery and social media management together in one Telegram bot.** Video publishing requires authorized media, appropriate permissions, and supported platform APIs.

**Version:** 0.3 | **Architecture:** Telegram-only · YouTube-first · Cloud-powered

# ⚡ PULSE — Telegram-only, YouTube-first

**Engineering build 0.3 (2026-10-09)**. The only normal user interface is the **Telegram bot**. There is no public PULSE website, local browser dashboard, Telegram Mini App, or frontend service in the default deployment. The program runs on an **always-on remote server**, independently of your PC. Database: cloud PostgreSQL (Neon); Redis is not required for bot dialog state.

## What works through the Telegram bot

- `/start` — main menu, owner-only access; `/status`, `/cancel`.
- **Sources** — paste `https://youtube.com/@channel`, `https://youtube.com/channel/UC...`, `https://youtube.com/watch?v=VIDEO_ID`, `https://youtube.com/shorts/VIDEO_ID`, or `https://youtu.be/VIDEO_ID`. You can also add supported Telegram links or save references to other social accounts.
- **YouTube Data API v3** — current public metadata for authorized requests (views, likes, comments, date, duration, source URL), repeatedly scanned with bounded history backfill, deduplication, and unknown-metric handling. **MP4 is NOT downloaded**. Deleted/private/region-restricted videos, quota exhaustion, and unavailable metrics are not guaranteed.
- **Filters** — SHORT/LONG/ALL duration *candidates*, minimum views, reactions and comments, maximum age and duration, per-source toggles.
- **Videos** — view statistics and open the original on YouTube; Telegram-native uploaded videos can be returned using Telegram `file_id` with no permanent copy on PULSE storage at ingestion.
- **Queues, accounts, routing and schedules** — owner-only inline navigation, account on/off, deletion/revocation of stored credentials, time presets and custom posting hours; any external publication is subject to provider permissions and actual video availability.
- **Notifications / diagnostics** — in-bot notification toggle, DB connectivity and YouTube key status.
- **Resilience** — PostgreSQL-persisted FSM (Redis DNS errors do not break Telegram commands), idempotent ingestion, source disable/removal, recovery after server restart via Docker restart policy.

## What cannot be guaranteed

A YouTube watch/Shorts URL **is not an MP4 stream or a distribution license**. YouTube Data API does not offer arbitrary video file downloads; the API developer policies prohibit downloading/storing YouTube audiovisual content without YouTube's prior written approval, and prohibit derived metrics from its API statistics unless allowed. Consequently PULSE **does not** automatically reproduce, clip, or upload arbitrary YouTube videos. It saves permitted metadata/URLs and can prepare MANUAL link handoff. For actual Instagram/TikTok publishing, provide your own authorized source media and platform approvals. YouTube `SHORT` mode is a **duration-based candidate**, not a verified Shorts classification. See [official policies](https://developers.google.com/youtube/terms/developer-policies).

A 24/7 Telegram bot needs an always-on host. Neon/Upstash do **not** host a Python process. This ZIP **is not deployed** to your VPS or Render/Railway account; actual cloud hosting must be provisioned and connected. OAuth for Instagram/TikTok unavoidably requires an external official provider authorization page and public HTTPS callback. Default Telegram-only deployment does **not** open HTTP ports; optional OAuth bridge has no dashboard and requires separate explicit setup.

## Remote installation (the only supported runtime)

Follow **[docs/DEPLOY_TELEGRAM_ONLY.md](docs/DEPLOY_TELEGRAM_ONLY.md)**. In summary:

1. Provide an always-on Linux VPS / always-on background worker with Docker and Compose.
2. Obtain bot token from [BotFather](https://t.me/BotFather), numeric Telegram owner ID, [YouTube Data API v3 key](https://console.cloud.google.com/apis/credentials), and Neon PostgreSQL connection URL. Never post these secrets in chats or git.
3. Upload the code to the remote server. Copy `.env.telegram.example` to **`backend/.env` on the SERVER**, fill credentials and Fernet key.
4. On the SERVER, run `docker compose -f docker-compose.telegram.yml up --build -d`. It automatically applies additive Alembic migrations, starts Telegram bot, YouTube source monitor and worker.
5. Find the bot in Telegram and press `/start`. There is **no `localhost:5173` or public dashboard**.

No video bytes from YouTube are stored. Do not extract this over the old Windows project: use a clean folder so obsolete local launchers cannot accidentally run. No API credentials are included in the repository.

## Testing

`cd backend && pip install -r requirements-dev.txt && pytest -q`

These tests verify pure logic, real FFmpeg where available, mock provider responses, migrations and static cloud configuration. **Live bot polling cannot be proven without installing dependencies and running with real Telegram credentials, and no real Instagram/TikTok posting was verified**. The deployment on a remote server is not included as an executed step. See [docs/ENGINEERING_AUDIT_TELEGRAM.md](docs/ENGINEERING_AUDIT_TELEGRAM.md).
# Мои аккаунты и публикации

Расширение сохраняет существующий Telegram-only runtime и Neon PostgreSQL.
В боте доступны подключения Instagram/TikTok OAuth, Telegram-каналов, групп и тем,
выбор нескольких направлений, независимые задания, расписания, пауза, описания и история.

Инструкции: [Telegram](docs/TELEGRAM_DESTINATIONS.md), [Instagram](docs/INSTAGRAM_SETUP.md),
[TikTok](docs/TIKTOK_SETUP.md), [deployment](docs/PUBLISHING_DEPLOYMENT.md), [аудит и ограничения](docs/PUBLISHING_AUDIT.md).
Если приложений Meta/TikTok и HTTPS-домена ещё нет: [первоначальная настройка OAuth](docs/OAUTH_FIRST_SETUP.md).
После регистрации приложений выполните `python scripts/pulse_local.py oauth-setup` для безопасного сохранения ключей.
Реальные публикации и серверное развёртывание требуют credentials и проверки владельцем.

