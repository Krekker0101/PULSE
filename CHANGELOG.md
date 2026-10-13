# PULSE Changelog

## 0.3.0 — Telegram-only / YouTube-first — 2026-10-09

- Deployable only as headless remote Telegram bot: no React, no Vite, no local Windows starter, no public dashboard.
- Main owner UX migrated to bot keyboards: source CRUD/disable, conditional filtering (views, reactions, comments, duration and age), Shorts candidates, pagination, manual scan, notifications, account access, posting schedule, routes, status, CSV reports.
- Direct YouTube URL ingestion: videos, Shorts, live and short youtu.be links through the official YouTube Data API; discover channel and store metadata, never arbitrary MP4.
- Bounded incremental older-video uploads backfill; duplicate-safe current+historic metrics monitoring.
- YT data compliance: no derived Viral Score; refresh time and stale metadata expiration; Aliembic revision `0003_freshness`.
- DB PostgreSQL FSM remains independent of Redis/Upstash DNS; no repeated HTTP `401` because HTTP UI is absent in default deployment.
- Headless runtime supervises bot, source watcher and worker; migration during container startup; secret-aware .dockerignore.
- Optional minimal OAuth callback/media bridge behind a named deployment profile (no admin website).
- New unit tests and clean and legacy schema migration checks; no live YouTube or Instagram/TikTok integration credentials bundled.
## Social destinations extension — 2026-10-10

- Added exact OAuth configuration diagnostics, in-bot setup help, safe oauth-setup command, and PUBLIC_BASE_URL preservation on repeated setup.

- Fixed partial-schema upgrade skipping videos.remote_video_id when sources.platform already existed; added additive repair migration 0005 and migration-before-start guard.

- Reused account, route, schedule and job identities for Telegram destinations and OAuth accounts.
- Added native chat selection, forum topics, rights checks, multiple destinations and confirmation.
- Added publishing pause, per-account caption/priority/timezone settings, attempts and media retention.
- Hardened OAuth granted scopes, token refresh/rotation, worker recovery and Instagram verification.
- Added additive migration 0004, contract/regression tests and provider/deployment instructions.

