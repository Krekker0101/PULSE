from functools import lru_cache
from pathlib import Path
from pydantic import Field, model_validator
from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    environment: str = 'development'
    bot_only: bool = False
    database_url: str = 'sqlite:///./pulse.db'
    database_url_unpooled: str = ''
    redis_url: str = 'redis://localhost:6379/0'
    youtube_api_key: str = ''
    tiktok_discovery_enabled: bool = False
    bot_token: str = ''
    owner_telegram_id: int = 0
    encryption_key: str = ''
    encryption_previous_keys: str = ''
    development_api_key: str = ''
    public_base_url: str = 'http://localhost:8000'
    mini_app_url: str = ''
    storage_dir: Path = Path('./storage')
    max_file_bytes: int = 500_000_000
    max_storage_bytes: int = Field(2_000_000_000, ge=50_000_000)
    media_retention_hours: int = Field(48, ge=24)
    source_scan_interval_seconds: int = Field(900, ge=60)
    worker_poll_seconds: int = Field(10, ge=1)
    instagram_app_id: str = ''
    instagram_app_secret: str = ''
    instagram_graph_version: str = 'v23.0'
    tiktok_client_key: str = ''
    tiktok_client_secret: str = ''
    tiktok_verified_media_domain: bool = False
    telegram_api_id: int = 0
    telegram_api_hash: str = ''
    telegram_session_encrypted: str = ''

    @model_validator(mode='after')
    def validate_production(self):
        if self.environment == 'production':
            if not self.bot_token or self.owner_telegram_id <= 0 or not self.encryption_key:
                raise ValueError('Production requires BOT_TOKEN, OWNER_TELEGRAM_ID and ENCRYPTION_KEY')
            if not self.database_url.startswith('postgresql+psycopg://'):
                raise ValueError('Production requires PostgreSQL (postgresql+psycopg://)')
            try: Fernet(self.encryption_key.encode())
            except (ValueError, TypeError): raise ValueError('ENCRYPTION_KEY must be a valid Fernet key') from None
            for previous in self.encryption_previous_keys.split(','):
                if previous.strip():
                    try: Fernet(previous.strip().encode())
                    except (ValueError, TypeError): raise ValueError('ENCRYPTION_PREVIOUS_KEYS must contain valid Fernet keys') from None
            if not self.bot_only and not self.public_base_url.startswith('https://'):
                raise ValueError('Production requires HTTPS PUBLIC_BASE_URL')
            if 'upstash.io' in self.redis_url and not self.redis_url.startswith('rediss://'):
                raise ValueError('Upstash Redis requires TLS (rediss://)')
            if self.development_api_key:
                raise ValueError('DEVELOPMENT_API_KEY is forbidden in production')
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
