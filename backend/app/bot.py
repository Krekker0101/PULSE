"""Multi-source owner Telegram UX. Dialog FSM persists in PostgreSQL, not Redis."""
import asyncio
import logging
from datetime import timezone
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from html import escape
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from .services.dialog_storage import PostgresFSMStorage
from .integrations.sources import normalize_public_source
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from sqlalchemy.orm import Session
from .config import get_settings
from .db import SessionLocal
from .models import User, Source, SocialAccount, Video, Route, Job, Metric
from .services.catalog import normalize_source, ingest, safe_source_rights
from .services.oauth import begin
from .services.scheduling import schedule_for_account

logger = logging.getLogger(__name__)
router = Router()


class Wizard(StatesGroup):
    source = State()
    min_views = State()
    rights = State()
    reactions = State()
    replies = State()
    age = State()
    duration = State()
    schedule_times = State()


def key(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t,d in row] for row in rows])


def nav():
    return [('⬅️ Назад', 'home')]


async def screen(obj, text: str, markup=None):
    try:
        if isinstance(obj, CallbackQuery):
            await obj.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            await obj.answer()
        else:
            await obj.answer(text, reply_markup=markup, disable_web_page_preview=True)
    except Exception as e:
        if isinstance(obj, CallbackQuery):
            await obj.answer()
            await obj.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
        else: raise


def owner_ok(telegram_id: int) -> bool:
    return telegram_id > 0 and telegram_id == get_settings().owner_telegram_id


def user_db(db: Session):
    user = db.query(User).filter_by(telegram_id=get_settings().owner_telegram_id).one_or_none()
    if not user:
        user = User(telegram_id=get_settings().owner_telegram_id)
        db.add(user);db.commit();db.refresh(user)
    return user


async def home(obj):
    with SessionLocal() as db:
        user=user_db(db)
        src=db.query(Source).filter(Source.owner_id==user.id,Source.access_status!='removed').count()
        accounts=db.query(SocialAccount).filter_by(owner_id=user.id,enabled=True).count()
        videos=db.query(Video).filter_by(owner_id=user.id,eligible=True).count()
        jobs=db.query(Job).filter(Job.owner_id==user.id,Job.status.in_(['SCHEDULED','WAITING_APPROVAL','PLATFORM_PROCESSING'])).count()
        status='🟢 Мониторинг активен' if user.monitoring_enabled else '⏸ Мониторинг приостановлен'
        rows=[ [('▶️ YouTube / Источники','sources'),('🔥 Найденное','videos')],
               [('🎯 Фильтры','filters'),('📱 Аккаунты','accounts')],
               [('🔀 Маршруты','routes'),('📤 Очередь','jobs')],
               [('🗓 Расписание','schedules'),('🎬 Форматы','formats')],
               [('🔄 Сканировать','scan:all'),('📊 Аналитика','analytics')],
               [('⚙️ Настройки','settings'),('❓ Помощь','help')],
               [('⏸ Пауза' if user.monitoring_enabled else '▶️ Запуск','monitor')]]
        keyboard=key(rows)
        await screen(obj, f'⚡ <b>PULSE</b>\nУправление видеоконтентом\n\n{status}\n📡 Источников: {src}\n📱 Аккаунтов: {accounts}\n🔥 Подходящих видео: {videos}\n⏳ В очереди: {jobs}', keyboard)


@router.message(CommandStart())
async def start(message: Message):
    if not owner_ok(message.from_user.id): return
    if message.chat.type != 'private':
        return await message.answer('Для подключения этой группы или темы отправьте /pulse_connect. Управление PULSE — в личном чате бота.')
    await home(message)


@router.message(Command('cancel'))
async def cancel(message: Message,state:FSMContext):
    if not owner_ok(message.from_user.id): return
    from aiogram.types import ReplyKeyboardRemove
    await state.clear(); await message.answer('Ввод отменён', reply_markup=ReplyKeyboardRemove()); await home(message)


@router.callback_query(F.data=='home')
async def to_home(callback:CallbackQuery,state:FSMContext):
    if not owner_ok(callback.from_user.id): return await callback.answer('Доступ запрещён',show_alert=True)
    await state.clear();await home(callback)


@router.callback_query(F.data=='monitor')
async def monitor(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id): return
    with SessionLocal() as db:
        u=user_db(db);u.monitoring_enabled=not u.monitoring_enabled;db.commit()
    await home(callback)


async def sources_page(callback:CallbackQuery,page:int=0):
    if not owner_ok(callback.from_user.id):return await callback.answer('Доступ запрещён',show_alert=True)
    with SessionLocal() as db:
        u=user_db(db)
        q=db.query(Source).filter(Source.owner_id==u.id,Source.access_status!='removed')
        total=q.count()
        page=max(0,min(page,max(0,(total-1)//8)))
        data=q.order_by(Source.id.desc()).offset(page*8).limit(8).all()
        lines=[f'📡 <b>Источники</b> · {total}',
               'YouTube — основной. Для других сетей доступ зависит от API и разрешений.']
        rows=[[('➕ Канал / Shorts / видео','source:add')]]
        for src in data:
            title=escape((src.title or src.username)[:48])
            lines.append(f'{"🟢" if src.enabled else "⏸"} {src.platform}: {title} · {src.format_mode}')
            rows.append([(f'⚙️ {src.platform}: {(src.title or src.username)[:32]}',f'source:{src.id}')])
        if total>8:
            pagination=[]
            if page:pagination.append(('◀️ Ранее',f'srcpage:{page-1}'))
            if page<(total-1)//8:pagination.append(('Далее ▶️',f'srcpage:{page+1}'))
            rows.append(pagination)
        rows.append(nav())
    await screen(callback,'\n'.join(lines),key(rows))


@router.callback_query(F.data=='sources')
async def list_sources(callback:CallbackQuery):
    await sources_page(callback,0)


@router.callback_query(F.data=='source:add')
async def source_add(callback:CallbackQuery,state:FSMContext):
    if not owner_ok(callback.from_user.id):return
    await state.set_state(Wizard.source)
    await screen(callback,'▶️ Пришлите ссылку YouTube-канала, отдельного видео или Shorts.\nТакже можно добавить Telegram-канал или аккаунт другой соцсети.\n\nПримеры:\nhttps://youtube.com/@handle\nhttps://youtube.com/shorts/VIDEO_ID\nhttps://youtube.com/watch?v=VIDEO_ID\n\n/cancel — отмена',key([nav()]))


@router.message(Wizard.source)
async def save_source(message:Message,state:FSMContext):
    if not owner_ok(message.from_user.id):return
    from .integrations.youtube import parse_youtube_video_url, get_public_video, message_id_from_remote_id, YouTubeError
    raw=(message.text or '').strip()
    video_id=parse_youtube_video_url(raw)
    if video_id:
        try:
            item, channel_id, title = await get_public_video(get_settings().youtube_api_key,video_id)
        except YouTubeError as exc:
            return await message.answer('⚠️ '+escape(str(exc))+'\nПроверьте ключ YouTube API. /cancel — отмена')
        with SessionLocal() as db:
            u=user_db(db)
            username='youtube:'+channel_id
            src=db.query(Source).filter_by(owner_id=u.id,username=username).first()
            if not src:
                src=Source(owner_id=u.id,username=username,platform='youtube',format_mode='SHORT' if '/shorts/' in raw else 'ALL',
                           canonical_url='https://www.youtube.com/channel/'+channel_id,
                           external_id=channel_id,title=title,access_status='youtube_api_active')
                db.add(src);db.flush()
            elif src.access_status=='removed':
                src.access_status='youtube_api_active';src.enabled=True
            ingest(db,src,message_id_from_remote_id(item.remote_id),item.published_at,
                   duration=item.duration,caption=item.caption,views=item.views,
                   reactions=item.likes,replies=item.comments,remote_video_id=item.remote_id,
                   source_url=item.url,thumbnail_url=item.thumbnail_url)
        await state.clear()
        await message.answer('✅ Видео YouTube сохранено в каталоге. Канал добавлен для дальнейшего отслеживания.\nВидео не скачивалось.')
        return await home(message)
    try: reference=normalize_public_source(raw)
    except ValueError as exc:
        return await message.answer(escape(str(exc)))
    with SessionLocal() as db:
        u=user_db(db)
        old=db.query(Source).filter_by(owner_id=u.id,username=reference.key).first()
        if old and old.access_status!='removed':
            return await message.answer('Этот источник уже добавлен. /cancel — отмена')
        if old:
            old.enabled=True;old.access_status=reference.status;old.scan_error=''
        else:
            src=Source(owner_id=u.id,username=reference.key,platform=reference.provider,
                       canonical_url=reference.url,access_status=reference.status,
                       format_mode=('SHORT' if reference.provider=='youtube' and raw.rstrip('/').endswith('/shorts') else 'ALL'))
            db.add(src)
        db.commit()
    await state.clear()
    await message.answer(f'✅ {reference.provider}: {reference.url} добавлен.\n{reference.note}')
    await home(message)


@router.callback_query(F.data.startswith('source:'))
async def source_details(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    try: source_id=int(callback.data.split(':')[1])
    except ValueError:return
    with SessionLocal() as db:
        u=user_db(db)
        s=db.query(Source).filter_by(id=source_id,owner_id=u.id).first()
        if not s:return await callback.answer('Источник не найден',show_alert=True)
        rows=([[('🔍 Проверить источник',f'scheck:{s.id}')]] if s.platform in ('youtube','instagram','tiktok') else []) + (
             [[('🔗 Подключить свой аккаунт',f'sbind:{s.id}')]] if s.platform in ('instagram','tiktok') else []) + [[('🎬 SHORT',f'sformat:{s.id}:SHORT'),('🎥 LONG',f'sformat:{s.id}:LONG'),('🔄 ALL',f'sformat:{s.id}:ALL')],
              [('👁 Просмотры',f'sfilter:{s.id}'),('❤️ Реакции',f'sreactions:{s.id}')],
              [('💬 Комментарии',f'sreplies:{s.id}'),('🕒 Возраст',f'sage:{s.id}')],
              [('⏱ Длительность',f'sduration:{s.id}')],
              [('📄 Подтвердить права',f'srights:{s.id}')],
              [('⏸ / ▶️',f'stoggle:{s.id}'),('🗑 Удалить',f'sdelete:{s.id}')], [('⬅️ Источники','sources')]]
        text=(f'📡 <b>{s.platform}: {escape(s.title or s.username)}</b>\nРежим: {s.format_mode}\nПросмотры: ≥ {s.min_views if s.min_views is not None else "не задано"}\n'
              f'Реакции: ≥ {s.min_reactions if s.min_reactions is not None else "—"} · комментарии: ≥ {s.min_replies if s.min_replies is not None else "—"}\nВозраст до: {s.max_age_hours or "∞"} ч\nДоступ: {s.access_status}\nОшибка: {escape(s.scan_error or "нет")}\nПрава: {s.rights_status}\nВидео: {db.query(Video).filter_by(source_id=s.id).count()}\n\n'
              'Верификация прав сейчас основана на заявлении владельца, не на независимой проверке.')
    await screen(callback,text,key(rows))


@router.callback_query(F.data.startswith('scheck:'))
async def youtube_check(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    from .integrations.youtube import YouTubeClient, YouTubeError
    from .integrations.connected_sources import ConnectedSourceError
    from .services.source_monitor import scan_youtube,scan_one
    sid=int(callback.data.split(':')[1])
    with SessionLocal() as db:
        u=user_db(db);src=db.query(Source).filter(Source.id==sid,Source.owner_id==u.id,Source.platform.in_(['youtube','instagram','tiktok'])).first()
        if src is None:return await callback.answer('Источник не найден',show_alert=True)
        try: count=(await scan_youtube(db,src,YouTubeClient(get_settings().youtube_api_key)) if src.platform=='youtube' else await scan_one(db,src))
        except (YouTubeError,ConnectedSourceError) as e:
            db.rollback()
            return await screen(callback,'⚠️ '+escape(str(e)),key([[('⬅️ К источнику',f'source:{sid}')]]))
    await screen(callback,f'✅ Источник проверен: {count} роликов обновлено (только метаданные).',
                 key([[('⬅️ К источнику',f'source:{sid}')]]))


@router.callback_query(F.data.startswith('sbind:'))
async def account_picker(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    sid=int(callback.data.split(':')[1])
    with SessionLocal() as db:
        u=user_db(db);source=db.query(Source).filter_by(id=sid,owner_id=u.id).first()
        if source is None or source.platform not in ('instagram','tiktok'):
            return await callback.answer('Источник не найден',show_alert=True)
        accounts=db.query(SocialAccount).filter_by(owner_id=u.id,provider=source.platform,enabled=True).all()
        rows=[[(f'{a.provider}: {a.username}'[:50],f'sbindsave:{sid}:{a.id}')] for a in accounts]
    if not rows:
        return await screen(callback,'Сначала подключите собственный аккаунт через OAuth в разделе «Аккаунты».',key([nav()]))
    await screen(callback,'Выберите собственный аккаунт с официальным разрешением на чтение видео:',key(rows+[[('⬅️ Назад',f'source:{sid}')]]))


@router.callback_query(F.data.startswith('sbindsave:'))
async def account_bind_save(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    _,sid,aid=callback.data.split(':')
    with SessionLocal() as db:
        u=user_db(db)
        src=db.query(Source).filter_by(id=int(sid),owner_id=u.id).first()
        account=db.query(SocialAccount).filter_by(id=int(aid),owner_id=u.id,enabled=True).first()
        if not src or not account or src.platform!=account.provider:
            return await callback.answer('Аккаунт недоступен',show_alert=True)
        if src.platform=='instagram' and src.username.split(':',1)[-1].lower()!=account.username.lower():
            return await callback.answer('Instagram профиль не совпадает с OAuth аккаунтом',show_alert=True)
        if src.platform=='tiktok' and 'video.list' not in account.scopes.replace(' ','').split(','):
            return await callback.answer('TikTok требует подтверждённое разрешение video.list',show_alert=True)
        src.bound_account_id=account.id;src.external_id=account.remote_id
        src.title=account.username;src.access_status='oauth_account_linked';src.scan_error=''
        if src.platform=='tiktok':
            state=src.provider_state or {}
            src.provider_state={**state,'declared_profile_url':src.canonical_url}
            src.canonical_url=''
        db.commit()
    await screen(callback,'✅ Собственный аккаунт привязан. Можно запустить проверку источника.',
                 key([[('🔍 Проверить',f'scheck:{sid}')],[('⬅️ К источнику',f'source:{sid}')]]))


@router.callback_query(F.data.startswith('sformat:'))
async def format_source(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    _,source_id,mode=callback.data.split(':')
    if mode not in ('SHORT','LONG','ALL'):return
    with SessionLocal() as db:
        u=user_db(db);s=db.query(Source).filter_by(owner_id=u.id,id=int(source_id)).first()
        if s:s.format_mode=mode;db.commit()
    await screen(callback,'✅ Формат обновлён',key([[('⚙️ К источнику',f'source:{source_id}')]]))


@router.callback_query(F.data.startswith('sfilter:'))
async def filter_source(callback:CallbackQuery,state:FSMContext):
    if not owner_ok(callback.from_user.id):return
    await state.update_data(source_id=int(callback.data.split(':')[1]))
    await state.set_state(Wizard.min_views)
    await screen(callback,'🎯 Отправьте минимальное количество просмотров (например: 50000).\n0 — отключить фильтр.',key([nav()]))


@router.message(Wizard.min_views)
async def save_filter(message:Message,state:FSMContext):
    if not owner_ok(message.from_user.id):return
    try:
        value=int((message.text or '').strip())
        if value<0 or value>10**12: raise ValueError
    except ValueError:return await message.answer('Введите целое число от 0 до 1 000 000 000 000.')
    data=await state.get_data();await state.clear()
    with SessionLocal() as db:
        u=user_db(db);s=db.query(Source).filter_by(owner_id=u.id,id=data['source_id']).first()
        if s:s.min_views=value or None;db.commit()
    await message.answer('✅ Фильтр сохранён. Недоступные просмотры не считаются нулём.')
    await home(message)


@router.callback_query(F.data.startswith('srights:'))
async def ask_rights(callback:CallbackQuery,state:FSMContext):
    if not owner_ok(callback.from_user.id):return
    await state.update_data(source_id=int(callback.data.split(':')[1]))
    await state.set_state(Wizard.rights)
    await screen(callback,'📄 Укажите основание права: вы автор, имеете лицензию, либо разрешение автора (название/ссылка/дата).\n'
                 'Сохранение означает ваше подтверждение, что имеете право размещать ролики в Instagram.\n'
                 'TikTok Direct Post для репостов не активируется.\n/cancel — отменить.',key([nav()]))


@router.message(Wizard.rights)
async def save_rights(message:Message,state:FSMContext):
    if not owner_ok(message.from_user.id):return
    evidence=(message.text or '').strip()
    if len(evidence)<10 or len(evidence)>3000:
        return await message.answer('Основание должно содержать от 10 до 3000 символов.')
    data=await state.get_data();await state.clear()
    with SessionLocal() as db:
        u=user_db(db);s=db.query(Source).filter_by(owner_id=u.id,id=data['source_id']).first()
        if s:
            s.rights_status='user_attested';s.rights_evidence=evidence;s.rights_instagram=True;s.rights_tiktok=True
            db.commit()
    await message.answer('✅ Сохранена ваша декларация прав на публикацию в Instagram. Независимая проверка документа не проводилась.')
    await home(message)


@router.callback_query(F.data.startswith('stoggle:'))
async def toggle_source(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    sid=int(callback.data.split(':')[1])
    with SessionLocal() as db:
        u=user_db(db);s=db.query(Source).filter_by(owner_id=u.id,id=sid).first()
        if s:s.enabled=not s.enabled;db.commit()
    await screen(callback,'✅ Состояние источника изменено',key([[('⚙️ К источнику',f'source:{sid}')]]))


@router.callback_query(F.data=='accounts')
async def list_accounts(callback:CallbackQuery):
    from .telegram_accounts import accounts_page
    await accounts_page(callback.model_copy(update={'data': 'accts:all:0'}))


@router.callback_query(F.data.startswith('oauth:'))
async def oauth_link(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    provider=callback.data.split(':')[1]
    from fastapi import HTTPException
    from .services.oauth import setup_message
    try:
        with SessionLocal() as db:
            u=user_db(db)
            url=begin(db,u.id,provider)
    except HTTPException as exc:
        if exc.status_code == 409 and isinstance(exc.detail, dict) and exc.detail.get('kind') == 'oauth_configuration':
            return await screen(callback,setup_message(provider,exc.detail['issues']),key([[('📖 Как настроить',f'oauthhelp:{provider}')],[('⬅️ Аккаунты','accounts')]]))
        return await screen(callback,'⚠️ Запрос авторизации недоступен. Проверьте платформу и права владельца.',key([nav()]))
    except Exception as exc:
        logger.error('OAuth start failed provider=%s type=%s',provider,type(exc).__name__)
        return await screen(callback,'⚠️ Не удалось создать сессию авторизации. Проверьте доступность БД и миграции; повторите позже.',key([[('⬅️ Аккаунты','accounts')]]))
    button=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'🔐 Подключить {provider.title()}',url=url)],
        [InlineKeyboardButton(text='⬅️ Назад',callback_data='accounts')]])
    await screen(callback,'Авторизуйтесь только на официальном сайте платформы. PULSE не запрашивает пароль.',button)


@router.callback_query(F.data=='formats')
async def formats(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    await screen(callback,'🎬 Формат каждого канала задаётся в карточке источника.\nSHORT: до 180 с; LONG: больше 180 с. Длительность можно уточнить там же.\n'
                        'Это фильтры обнаружения, а не гарантии публикации.',key([[('📡 Источники','sources')],nav()]))


@router.callback_query(F.data=='filters')
async def filters(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    await screen(callback,'🎯 Откройте источник: можно настроить просмотры, реакции, комментарии, возраст и длительность роликов.\n'
                        'Отсутствующие метрики отправляются на проверку, не превращаются в ноль.',key([[('📡 Источники','sources')],nav()]))


async def videos_page(callback:CallbackQuery,page:int=0):
    if not owner_ok(callback.from_user.id):return await callback.answer('Доступ запрещён',show_alert=True)
    with SessionLocal() as db:
        u=user_db(db)
        q=db.query(Video).filter_by(owner_id=u.id)
        total=q.count();page=max(0,min(page,max(0,(total-1)//8)))
        items=q.order_by(Video.discovered_at.desc(),Video.id.desc()).offset(page*8).limit(8).all()
        lines=[f'🔥 <b>Найденные видео</b> · {total}']
        rows=[]
        for v in items:
            title=escape((v.original_caption or 'Без описания').splitlines()[0][:42])
            lines.append(f'#{v.id} · {title} · 👁 {v.views if v.views is not None else "?"} · {"✅" if v.eligible else "⏳"}')
            rows.append([(f'🎬 Видео #{v.id}',f'video:{v.id}')])
        if total>8:
            buttons=[]
            if page:buttons.append(('◀️ Ранее',f'vidpage:{page-1}'))
            if page<(total-1)//8:buttons.append(('Далее ▶️',f'vidpage:{page+1}'))
            rows.append(buttons)
    await screen(callback,'\n'.join(lines) if items else 'Пока видео нет. Добавьте YouTube-канал и настройте YOUTUBE_API_KEY на сервере.',key(rows+[nav()]))


@router.callback_query(F.data=='videos')
async def list_videos(callback:CallbackQuery):
    await videos_page(callback,0)


@router.callback_query(F.data.startswith('video:'))
async def video_details(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    with SessionLocal() as db:
        u=user_db(db);v=db.query(Video).filter_by(owner_id=u.id,id=int(callback.data.split(':')[1])).first()
        if not v:return await callback.answer('Не найдено',show_alert=True)
        s=db.get(Source,v.source_id)
        link=v.source_url or (f'https://t.me/{s.username}/{v.message_id}' if s.platform=='telegram' and not s.username.startswith('owner-upload') else '')
        text=(f'🎬 <b>Видео #{v.id}</b>\n{escape((v.original_caption or "Без названия").splitlines()[0][:200])}\nИсточник: {escape(s.title or s.username)} ({s.platform})\nПросмотры: {v.views if v.views is not None else "неизвестно"}\n'
              f'Реакции: {v.reactions if v.reactions is not None else "неизвестно"}\nОтветы: {v.replies if v.replies is not None else "неизвестно"}\n'
              f'Viral Score: {v.viral_score if v.viral_score is not None else "недостаточно данных"}\n'
              f'Подходит: {v.eligible}\n{v.filter_reason[:200]}')
    rows=[]
    if link.startswith('https://'):rows.append([InlineKeyboardButton(text='▶️ Открыть YouTube / оригинал',url=link)])
    if v.telegram_file_id:rows.append([InlineKeyboardButton(text='📥 Получить в Telegram',callback_data=f'sendmedia:{v.id}')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад',callback_data='videos')])
    await screen(callback,text,InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data=='routes')
async def route_menu(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    with SessionLocal() as db:
        u=user_db(db);sources=db.query(Source).filter_by(owner_id=u.id,enabled=True).all()
        routes=db.query(Route).filter_by(owner_id=u.id).all()
        links=[(r.id,db.get(Source,r.source_id),db.get(SocialAccount,r.account_id)) for r in routes]
    rows=[[(f'➕ {s.platform}: {escape(s.title or s.username)}'[:54],f'route:source:{s.id}')] for s in sources[:10]]
    rows.extend([[(f'⏸ {esc_src.title or esc_src.username} → {esc_acc.username}'[:55],f'routedisable:{rid}'), ('?????',f'routemode:{rid}')]
                 for rid,esc_src,esc_acc in links if esc_src and esc_acc])
    await screen(callback,f'🔀 Маршрутов: {len(routes)}\nВыберите источник для нового маршрута или нажмите на существующий, чтобы отключить:',
                 key(rows+[nav()]))


@router.callback_query(F.data.startswith('route:source:'))
async def route_account(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    source_id=int(callback.data.split(':')[-1])
    with SessionLocal() as db:
        u=user_db(db)
        if not db.query(Source).filter_by(id=source_id,owner_id=u.id).first():return
        accounts=db.query(SocialAccount).filter_by(owner_id=u.id,enabled=True).all()
    await screen(callback,'Выберите целевой аккаунт (Instagram: одобрение; TikTok: черновик для автора):',
                 key([[(a.provider+' '+a.username,f'route:save:{source_id}:{a.id}')] for a in accounts]+[[('⬅️ Маршруты','routes')]]))


@router.callback_query(F.data.startswith('route:save:'))
async def route_save(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    _,_,sid,aid=callback.data.split(':')
    with SessionLocal() as db:
        u=user_db(db)
        source=db.query(Source).filter_by(id=int(sid),owner_id=u.id).first()
        account=db.query(SocialAccount).filter_by(id=int(aid),owner_id=u.id,enabled=True).first()
        if not source or not account:return await callback.answer('Некорректная настройка',show_alert=True)
        if db.query(Route).filter_by(owner_id=u.id,source_id=source.id,account_id=account.id).first():
            return await callback.answer('Маршрут уже существует',show_alert=True)
        mode = 'MANUAL' if source.platform!='telegram' else ('DRAFT' if account.provider=='tiktok' else 'APPROVAL')
        db.add(Route(owner_id=u.id,source_id=source.id,account_id=account.id,mode=mode))
        db.commit()
    await screen(callback,'✅ Маршрут добавлен: '+('ссылка для ручной публикации (без скачивания видео)' if mode=='MANUAL' else mode),key([nav()]))


@router.callback_query(F.data=='jobs')
async def queue(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    with SessionLocal() as db:
        u=user_db(db);jobs=db.query(Job).filter_by(owner_id=u.id).order_by(Job.created_at.desc()).limit(12).all()
    await screen(callback,'📤 <b>Очередь</b>\n'+ '\n'.join(f'#{j.id}: {j.status}' for j in jobs) if jobs else '📤 Очередь пуста',
                 key([[(f'Задание #{j.id}',f'job:{j.id}')] for j in jobs]+[nav()]))


@router.callback_query(F.data.startswith('job:'))
async def job_details(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    with SessionLocal() as db:
        u=user_db(db);j=db.query(Job).filter_by(id=int(callback.data.split(':')[1]),owner_id=u.id).first()
        if not j:return await callback.answer('Не найдено',show_alert=True)
        acc=db.get(SocialAccount,j.account_id)
        info=f'📤 Задание #{j.id}\nАккаунт: {acc.provider} @{acc.username}\nСостояние: {j.status}\nОшибка: {escape(j.error) if j.error else "нет"}'
        rows=[]
        if j.mode=='MANUAL' and j.approved:
            video=db.get(Video,j.video_id)
            if video and video.telegram_file_id: rows.append([('📥 Получить исходный файл',f'sendmedia:{j.video_id}')])
            info += '\nОписание: '+escape(j.caption[:500])
            if video and video.source_url: info += '\nИсходная ссылка: '+escape(video.source_url)
        if j.status=='WAITING_APPROVAL' and (acc.provider in ('instagram','telegram') or j.mode in ('DRAFT','MANUAL')):rows.append([('✅ Одобрить',f'approve:{j.id}')])
        if j.status in ('WAITING_APPROVAL','SCHEDULED'):rows.append([('❌ Отменить',f'canceljob:{j.id}')])
        rows.append([('⬅️ Очередь','jobs')])
    await screen(callback,info,key(rows))


@router.callback_query(F.data.startswith('approve:'))
async def approve_job(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    jid=int(callback.data.split(':')[1])
    with SessionLocal() as db:
        u=user_db(db);j=db.query(Job).filter_by(id=jid,owner_id=u.id,status='WAITING_APPROVAL').first()
        if not j:return await callback.answer('Недоступно',show_alert=True)
        v=db.get(Video,j.video_id);s=db.get(Source,v.source_id);a=db.get(SocialAccount,j.account_id)
        if not a.enabled or not safe_source_rights(s,a.provider) or (a.provider=='tiktok' and j.mode not in ('DRAFT','MANUAL')):
            return await callback.answer('Не подтверждены права / подключение',show_alert=True)
        j.approved=True
        if j.mode=='MANUAL':
            j.status='READY';j.error='Публикуйте самостоятельно: см. исходный Telegram-пост.'
        else:
            j.status='SCHEDULED';j.scheduled_at=schedule_for_account(db,u.id,a.id)
        db.commit()
    await screen(callback,'✅ Публикация добавлена в очередь. Результат зависит от API Instagram.',key([[('📤 Очередь','jobs')]]))


@router.callback_query(F.data.startswith('canceljob:'))
async def cancel_job(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    with SessionLocal() as db:
        u=user_db(db);j=db.query(Job).filter_by(id=int(callback.data.split(':')[1]),owner_id=u.id).first()
        if j and j.status in ('WAITING_APPROVAL','SCHEDULED'):
            j.status='CANCELLED';db.commit()
    await queue(callback)


@router.callback_query(F.data=='analytics')
async def analytics(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    with SessionLocal() as db:
        u=user_db(db)
        count=lambda model,**kwargs:db.query(model).filter_by(owner_id=u.id,**kwargs).count()
        text=f'📊 <b>Аналитика</b>\nВидео найдено: {count(Video)}\nПодходящих: {count(Video,eligible=True)}\nОпубликовано: {count(Job,status="PUBLISHED")}\nОшибок: {count(Job,status="FAILED")}'
    await screen(callback,text,key([[('📥 CSV отчёт','analytics:csv')],nav()]))


@router.callback_query(F.data=='settings')
async def settings(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    with SessionLocal() as db:
        u=user_db(db)
        notice='🔔 Включены' if u.notify_enabled else '🔕 Отключены'
    await screen(callback,'⚙️ Настройки PULSE\nВесь интерфейс — в Telegram.\nЧасовой пояс: Asia/Dushanbe.\nОблачные ключи задаются при установке на сервер.\nУведомления: '+notice,
                 key([[('🔔 Переключить уведомления','notifications:toggle')],[('🩺 Диагностика','diagnostics')],nav()]))


@router.callback_query(F.data=='help')
async def help_screen(callback:CallbackQuery):
    if not owner_ok(callback.from_user.id):return
    await screen(callback,'❓ <b>PULSE</b>\n1. Добавьте источник.\n2. Дайте боту доступ к новым постам канала.\n'
                        '3. Выберите формат и метрики.\n4. Подтвердите законные права на публикацию.\n'
                        '5. Подключите аккаунт OAuth.\n6. Создайте маршрут.\n\n'
                        'YouTube сканируется официальным Data API при наличии YOUTUBE_API_KEY. Instagram/TikTok профили требуют отдельных прав.\n'
                        'Важно: Bot API не читает историю чужих каналов и не предоставляет полные метрики.\n'
                        'YouTube: получаем метаданные и ссылки, но не произвольные MP4. Не гарантируем доступ к приватным видео и не копируем без разрешения.\n'
        'PULSE запускается на сервере, а управляется через Telegram.\n'
        'Нельзя публиковать чужие видео без разрешения.',key([nav()]))


@router.channel_post()
async def channel_post(message:Message):
    video = message.video or message.animation or (message.document if (message.document and (message.document.mime_type or '').startswith('video/')) else None)
    if not video:return
    with SessionLocal() as db:
        u=user_db(db)
        if not u.monitoring_enabled:return
        src=db.query(Source).filter_by(owner_id=u.id,chat_id=message.chat.id,enabled=True).one_or_none()
        if not src and message.chat.username:
            src=db.query(Source).filter_by(owner_id=u.id,username=message.chat.username.lower(),platform='telegram',enabled=True).first()
        if not src:return
        src.chat_id=message.chat.id;src.title=message.chat.title or '';src.access_status='bot_updates'
        r=getattr(message,'reactions',None)
        reactions=sum(x.total_count for x in r.reactions) if r and getattr(r,'reactions',None) else None
        # Bot API generally does not supply channel post views. Remain UNKNOWN.
        record, created=ingest(db,src,message.message_id,message.date,
                    file_id=video.file_id,duration=getattr(video,'duration',None),
                    width=getattr(video,'width',None),height=getattr(video,'height',None),
                    caption=message.caption or '',views=None,reactions=reactions,replies=None)
        logger.info('Channel post ingested: source=%s video=%s new=%s',src.id,record.id,created)


async def run():
    settings=get_settings()
    if not settings.bot_token or not settings.owner_telegram_id:
        raise RuntimeError('BOT_TOKEN and OWNER_TELEGRAM_ID required for bot')
    # Durable state in Neon/PostgreSQL; redis DNS/outages never break Telegram commands.
    storage=PostgresFSMStorage()
    bot=Bot(settings.bot_token,default=DefaultBotProperties(parse_mode='HTML'))
    from aiogram.fsm.storage.memory import SimpleEventIsolation
    dp=Dispatcher(storage=storage,events_isolation=SimpleEventIsolation())
    from .telegram_accounts import accounts_router
    dp.include_router(accounts_router)
    dp.include_router(router)
    from .telegram_ui_extra import extra_router
    dp.include_router(extra_router)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands([BotCommand(command='start',description='Открыть PULSE'),
                                   BotCommand(command='status',description='Статус мониторинга'),
                                   BotCommand(command='cancel',description='Отменить ввод')])
        await dp.start_polling(bot,handle_signals=False,allowed_updates=['message','callback_query','channel_post','my_chat_member'])
    finally:
        await bot.session.close();await storage.close()


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
