# PULSE 0.3 — engineering verification / known limits

Status date: 2026-10-09. This document separates verified implementation from live external evidence.

## Verified in available environment

- Cloud-bot Compose config is parseable and runs no frontend/API/UI service by default; optional OAuth bridge requires an explicit profile.
- PostgreSQL-backed Telegram dialogue storage code (no runtime Redis calls in standard bot FSM).
- YouTube Data API v3 client URL parsing, channel uploads scans, gradual historical page scan, directly requested video IDs, metadata and unknown-metric representation in offline HTTP fixtures.
- Bot-only production settings, YouTube data retention of stale API stats/metadata, no derived YouTube Viral Score, and existing domain tests.
- Existing SQLAlchemy/Alembic migrations and Python syntax verified; unit/integration tests completed.

## Not verified / external prerequisites

- **Remote deployment** — not yet deployed to a connected VPS/Render/Railway; requires an always-on service account.
- **Real YouTube key** — no user API key in test environment; actual public YouTube requests and quotas unverified.
- **Real Telegram polling** — cannot start without Bot Token / network, `aiogram` installation missing from offline test container (dependency listed for host).
- **Instagram/TikTok Direct Post** — requires provider approval, credentials, consent, media rights, and optional HTTPS bridge; no live publication verified.
- **Fetching arbitrary YouTube MP4** — NOT implemented and not offered by YouTube Data API. Cannot be guaranteed for every video; do not use YouTube API to facilitate unapproved downloading.
- **True Shorts classification** — upload playlist metadata does not include verified vertical orientation; duration-based filtering is approximate.
- **Background cloud uptime** — requires always-on hosting; storage providers alone do not provide compute.

## Engineering findings and mitigations

| Severity | Finding | Mitigation |
|---|---|---|
| High | Old Mini App requests caused repeated `401 Unauthorized` outside Telegram | Telegram-only Compose has no UI server, no HTTP API and no frontend |
| High | Upstash DNS failures broke aiogram FSM state lookups | PostgreSQL FSM storage; Redis no longer used by Telegram bot |
| High | Legacy bot exposed settings only in Mini App | Source/video pagination, metrics, schedule, account, routing, notification, diagnostics controls moved into Telegram |
| High | Arbitrary YouTube MP4 download implied by requirements | Official metadata-only support; true media ingestion via separate rights-authorized Telegram uploads |
| High | Derived Viral Score from YouTube Data API | Disabled; raw API metrics and basic condition checks only |
| Medium | No ingestion by a single Shorts/video URL | Strict parser and videos.list by video ID + channel ingestion |
| Medium | Channel history capped at first 50 uploads | Bounded incremental playlist backfill, stored cursor |
| Medium | YouTube metadata could remain indefinitely | 29-day stale metadata scrub and expired metric snapshot deletion |
| Medium | Default dashboard had no pagination | 8 items/page with inline navigation |
| Medium | Deleting sources left future jobs queued | Disable routing and cancel not-yet-started jobs |

## Remaining technical risks

- Third-party API behavior and quotas are subject to change.
- The legacy API module remains in the repository for OAuth-capable backend logic, but is **not served** by the default deployment. The optional minimal OAuth bridge exposes callback/media endpoints if enabled.
- The Telegram screens were statically parsed; full aiogram runtime E2E cannot be certified in this offline build environment.
- In the existing worker, actual publication confirmation and ambiguity reconciliation still need live platform credentials and tests. Do not advertise guaranteed publishing.
- Source discovery via YouTube is not exhaustive if API video metadata is private, removed, blocked or quota-limited. Incremental backfill progresses as recurring scans run.
- The original UX supports manually attested content rights; independent licensing verification remains outside scope.
