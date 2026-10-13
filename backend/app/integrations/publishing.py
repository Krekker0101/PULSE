"""Platform adapter contract: capability check, dispatch, and status reconciliation."""
from typing import Protocol
from .social import Instagram, TikTok


class PublishingAdapter(Protocol):
    async def dispatch(self, **kwargs) -> dict: ...


class TelegramPublishingAdapter:
    def __init__(self, bot):
        self.bot = bot

    async def dispatch(self, *, target, file_id, caption, media_kind='video'):
        args = {'chat_id': target.chat_id, 'message_thread_id': target.message_thread_id, 'parse_mode': None}
        if media_kind == 'document':
            message = await self.bot.send_document(document=file_id, caption=caption[:1024], **args)
        elif media_kind == 'photo':
            message = await self.bot.send_photo(photo=file_id, caption=caption[:1024], **args)
        elif media_kind == 'text':
            message = await self.bot.send_message(text=caption[:4096], **args)
        else:
            message = await self.bot.send_video(video=file_id, caption=caption[:1024], supports_streaming=True, **args)
        chat = message.chat
        url = f'https://t.me/{chat.username}/{message.message_id}' if chat.username else None
        return {'id': str(message.message_id), 'url': url, 'date': message.date}

    async def album(self, target, media):
        return await self.bot.send_media_group(chat_id=target.chat_id, message_thread_id=target.message_thread_id, media=media)


class InstagramPublishingAdapter:
    async def dispatch(self, *, token, remote_id, url, caption):
        return {'id': await Instagram().create_reel_container(token, remote_id, url, caption)}


class TikTokPublishingAdapter:
    async def dispatch(self, *, token, url, **kwargs):
        return {'id': await TikTok().initialize_draft_from_verified_url(token, url)}
