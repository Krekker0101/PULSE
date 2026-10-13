from datetime import datetime, timezone
from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        JSON, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default='Owner')
    timezone: Mapped[str] = mapped_column(String(64), default='Asia/Dushanbe')
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Source(Base):
    __tablename__ = 'sources'
    __table_args__ = (UniqueConstraint('owner_id', 'username'), UniqueConstraint('owner_id', 'chat_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    username: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(32), default='telegram', server_default='telegram', index=True)
    bound_account_id: Mapped[int | None] = mapped_column(ForeignKey('social_accounts.id'), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    canonical_url: Mapped[str] = mapped_column(Text, default='', server_default='')
    provider_state: Mapped[dict] = mapped_column(JSON, default=dict)
    scan_error: Mapped[str] = mapped_column(Text, default='', server_default='')
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(250), default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    access_status: Mapped[str] = mapped_column(String(32), default='awaiting_bot_admin')
    rights_status: Mapped[str] = mapped_column(String(32), default='unverified')
    rights_evidence: Mapped[str] = mapped_column(Text, default='')
    rights_derivative: Mapped[bool] = mapped_column(Boolean, default=False)
    rights_instagram: Mapped[bool] = mapped_column(Boolean, default=False)
    rights_tiktok: Mapped[bool] = mapped_column(Boolean, default=False)
    format_mode: Mapped[str] = mapped_column(String(12), default='ALL')
    min_views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    min_reactions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_replies: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_age_hours: Mapped[int | None] = mapped_column(Integer, default=48)
    min_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    filter_tree: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Video(Base):
    __tablename__ = 'videos'
    __table_args__ = (UniqueConstraint('source_id', 'message_id'), Index('ix_video_owner_eligible', 'owner_id', 'eligible'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey('sources.id'), index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    remote_video_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, default='', server_default='')
    thumbnail_url: Mapped[str] = mapped_column(Text, default='', server_default='')
    telegram_file_id: Mapped[str | None] = mapped_column(Text)
    original_caption: Mapped[str] = mapped_column(Text, default='')
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(BigInteger)
    reactions: Mapped[int | None] = mapped_column(BigInteger)
    replies: Mapped[int | None] = mapped_column(BigInteger)
    viral_score: Mapped[float | None] = mapped_column(Float)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_reason: Mapped[str] = mapped_column(String(300), default='Not evaluated')
    media_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    media_info: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[Source] = relationship()


class ClipAsset(Base):
    __tablename__ = 'clip_assets'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey('videos.id'), index=True)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    media_path: Mapped[str] = mapped_column(Text)
    media_info: Mapped[dict | None] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class Metric(Base):
    __tablename__ = 'metrics'
    __table_args__ = (Index('ix_metric_video_time', 'video_id', 'at'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey('videos.id'), index=True)
    views: Mapped[int | None] = mapped_column(BigInteger)
    reactions: Mapped[int | None] = mapped_column(BigInteger)
    replies: Mapped[int | None] = mapped_column(BigInteger)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialAccount(Base):
    __tablename__ = 'social_accounts'
    __table_args__ = (UniqueConstraint('owner_id', 'provider', 'remote_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    remote_id: Mapped[str] = mapped_column(String(200))
    username: Mapped[str] = mapped_column(String(200), default='')
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str] = mapped_column(Text, default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    connection_status: Mapped[str] = mapped_column(String(32), default='CONNECTED', server_default='CONNECTED')
    publishing_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    settings: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthFlow(Base):
    __tablename__ = 'oauth_flows'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    provider: Mapped[str] = mapped_column(String(16))
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class Route(Base):
    __tablename__ = 'routes'
    __table_args__ = (UniqueConstraint('owner_id', 'source_id', 'account_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey('sources.id'))
    account_id: Mapped[int] = mapped_column(ForeignKey('social_accounts.id'))
    mode: Mapped[str] = mapped_column(String(16), default='APPROVAL')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AccountSchedule(Base):
    __tablename__ = 'account_schedules'
    __table_args__ = (UniqueConstraint('owner_id', 'account_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('social_accounts.id'))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(64), default='Asia/Dushanbe')
    days: Mapped[list] = mapped_column(JSON, default=lambda: [0,1,2,3,4,5,6])
    times: Mapped[list] = mapped_column(JSON, default=lambda: ['10:00','15:00','20:00'])
    daily_max: Mapped[int] = mapped_column(Integer, default=3)
    min_gap_minutes: Mapped[int] = mapped_column(Integer, default=120)


class Job(Base):
    __tablename__ = 'jobs'
    __table_args__ = (UniqueConstraint('video_id', 'account_id'), Index('ix_job_ready', 'status', 'scheduled_at'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey('videos.id'))
    account_id: Mapped[int] = mapped_column(ForeignKey('social_accounts.id'))
    mode: Mapped[str] = mapped_column(String(16), default='APPROVAL')
    status: Mapped[str] = mapped_column(String(32), default='WAITING_APPROVAL')
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    caption: Mapped[str] = mapped_column(Text, default='')
    platform_id: Mapped[str | None] = mapped_column(String(200))
    platform_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DialogState(Base):
    __tablename__ = 'dialog_states'
    identity: Mapped[str] = mapped_column(String(256), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(256), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'),index=True)
    event_key: Mapped[str] = mapped_column(String(120),unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default='')
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Reuse the account identity for routes, schedules and jobs; no duplicate queue.
class TelegramDestinationChat(Base):
    __tablename__ = 'telegram_destination_chats'
    account_id: Mapped[int] = mapped_column(ForeignKey('social_accounts.id'), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(250), default='')
    message_thread_id: Mapped[int | None] = mapped_column(Integer)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)


class PublishingAttempt(Base):
    __tablename__ = 'publishing_attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(64), default='STARTED')
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
