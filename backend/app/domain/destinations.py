"""Uniform read model over legacy account identity, chat details and schedule."""
from dataclasses import dataclass
from datetime import datetime
from ..models import AccountSchedule, TelegramDestinationChat


@dataclass(frozen=True)
class PublishingDestination:
    id: int
    owner_id: int
    platform: str
    external_account_id: str | None
    external_chat_id: int | None
    display_name: str
    username: str
    connection_status: str
    capabilities: dict
    permissions: dict
    publishing_enabled: bool
    publishing_mode: str
    schedule_id: int | None
    timezone: str
    default_caption_template: str
    created_at: datetime
    last_verified_at: datetime | None


def destination(db, account):
    chat = db.get(TelegramDestinationChat, account.id) if account.provider == 'telegram' else None
    schedule = db.query(AccountSchedule).filter_by(owner_id=account.owner_id, account_id=account.id).first()
    return PublishingDestination(id=account.id, owner_id=account.owner_id, platform=account.provider,
        external_account_id=None if chat else account.remote_id, external_chat_id=chat.chat_id if chat else None,
        display_name=chat.title if chat else account.username, username=account.username,
        connection_status=account.connection_status, capabilities=account.capabilities or {},
        permissions=chat.permissions if chat else {'scopes': account.scopes.split(',')},
        publishing_enabled=account.publishing_enabled, publishing_mode='DRAFT' if account.provider == 'tiktok' else 'APPROVAL',
        schedule_id=schedule.id if schedule else None, timezone=schedule.timezone if schedule else 'Asia/Dushanbe',
        default_caption_template=(account.settings or {}).get('caption_template', '{caption}'),
        created_at=account.created_at, last_verified_at=account.last_verified_at)
