"""Owner-only Telegram management flows: no Mini App or dashboard required."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from html import escape
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, text
from .config import get_settings
from .db import SessionLocal
from .models import Source, Video, Job, User, Metric, Route
from .integrations.youtube import parse_youtube_video_url
from .services.catalog import ingest
from .bot import Wizard, owner_ok, user_db, screen, key, home, save_source
from .services.source_monitor import scan_all_once

logger = logging.getLogger(__name__)
extra_router = Router()


async def _select_number(cb: CallbackQuery, state: FSMContext, *, field: str, state_name, hint: str):
    if not owner_ok(cb.from_user.id):
        return await cb.answer('Недостаточно прав', show_alert=True)
    try:
        sid = int(cb.data.split(':')[1])
    except (IndexError, ValueError):
        return await cb.answer('Некорректный источник', show_alert=True)
    with SessionLocal() as db:
        user = user_db(db)
        if not db.query(Source).filter_by(owner_id=user.id, id=sid).first():
            return await cb.answer('Источник не найден', show_alert=True)
    await state.update_data(source_id=sid, numeric_field=field)
    await state.set_state(state_name)
    await screen(cb, hint+'\n\n0 — снять ограничение. /cancel — отменить.', key([[('⬅️ Главная', 'home')]]))


@extra_router.callback_query(F.data.startswith('sreactions:'))
async def ask_reactions(cb: CallbackQuery, state: FSMContext):
    await _select_number(cb,state,field='min_reactions',state_name=Wizard.reactions,
                         hint='❤️ Укажите минимум реакций/лайков на видео (число).')


@extra_router.callback_query(F.data.startswith('sreplies:'))
async def ask_replies(cb: CallbackQuery, state: FSMContext):
    await _select_number(cb,state,field='min_replies',state_name=Wizard.replies,
                         hint='💬 Укажите минимум комментариев (число).')


@extra_router.callback_query(F.data.startswith('sage:'))
async def ask_age(cb: CallbackQuery, state: FSMContext):
    await _select_number(cb,state,field='max_age_hours',state_name=Wizard.age,
                         hint='🕒 Максимальный возраст публикации в часах: например 24, 48, 168.')


@extra_router.callback_query(F.data.startswith('sduration:'))
async def ask_duration(cb: CallbackQuery, state: FSMContext):
    await _select_number(cb,state,field='max_duration',state_name=Wizard.duration,
                         hint='⏱ Максимальная длительность видео в секундах (60, 90, 180...).')


@extra_router.message(StateFilter(Wizard.reactions, Wizard.replies, Wizard.age, Wizard.duration))
async def save_number(message: Message, state: FSMContext):
    if not message.from_user or not owner_ok(message.from_user.id):
        return
    data=await state.get_data()
    field=data.get('numeric_field')
    if field not in {'min_reactions','min_replies','max_age_hours','max_duration'}:
        return await message.answer('Состояние формы устарело. /cancel')
    try:
        value=int((message.text or '').strip())
        if value < 0 or value > (10**12 if field=='min_reactions' else 10**9 if field=='min_replies' else 10**7):
            raise ValueError
    except ValueError:
        return await message.answer('Введите целое число в допустимом диапазоне либо 0.')
    with SessionLocal() as db:
        user=user_db(db)
        src=db.query(Source).filter_by(id=data.get('source_id'),owner_id=user.id).first()
        if not src:
            await state.clear()
            return await message.answer('Источник больше недоступен.')
        setattr(src, field, value or None)
        db.commit()
    await state.clear()
    await message.answer('✅ Фильтр сохранён. Метрики будут перепроверены при следующем сканировании.')
    await home(message)


@extra_router.callback_query(F.data.startswith('sdelete:'))
async def ask_remove(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id): return await cb.answer('Недостаточно прав',show_alert=True)
    try: sid=int(cb.data.split(':')[1])
    except (IndexError, ValueError): return await cb.answer('Неверный источник',show_alert=True)
    with SessionLocal() as db:
        u=user_db(db);src=db.query(Source).filter_by(id=sid,owner_id=u.id).first()
        if not src:return await cb.answer('Источник не найден',show_alert=True)
        title=escape(src.title or src.username)
    await screen(cb, f'🗑 Убрать {title} из мониторинга?\nИстория видео останется в базе; незапущенные публикации отменятся.',
                 key([[('🗑 Да, убрать',f'sdeleteconfirm:{sid}')],[('Отмена',f'source:{sid}')]]))


@extra_router.callback_query(F.data.startswith('sdeleteconfirm:'))
async def remove_source(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id): return await cb.answer('Недостаточно прав',show_alert=True)
    try: sid=int(cb.data.split(':')[1])
    except (IndexError, ValueError):return await cb.answer('Неверный источник',show_alert=True)
    with SessionLocal() as db:
        u=user_db(db)
        src=db.query(Source).filter_by(id=sid,owner_id=u.id).first()
        if not src:return await cb.answer('Источник не найден',show_alert=True)
        src.enabled=False;src.access_status='removed'
        for r in db.query(Route).filter_by(owner_id=u.id,source_id=sid).all():r.enabled=False
        vids=db.query(Video.id).filter_by(owner_id=u.id,source_id=sid)
        for job in db.query(Job).filter(Job.owner_id==u.id,Job.video_id.in_(vids),
                                       Job.status.in_(['WAITING_APPROVAL','SCHEDULED'])).all():
            job.status='CANCELLED';job.error='Источник удалён из мониторинга.'
        db.commit()
    await screen(cb,'✅ Источник убран из мониторинга. При повторном добавлении его можно восстановить.',key([[('📡 Источники','sources')]]))


@extra_router.callback_query(F.data=='notifications:toggle')
async def notifications(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    with SessionLocal() as db:
        u=user_db(db)
        u.notify_enabled=not u.notify_enabled
        value=u.notify_enabled
        db.commit()
    await screen(cb, '🔔 Уведомления '+('включены' if value else 'отключены'),
                 key([[('⚙️ Настройки','settings')],[('🏠 Главная','home')]]))


@extra_router.callback_query(F.data=='diagnostics')
async def diagnostics(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    try:
        with SessionLocal() as db:
            db.execute(text('SELECT 1'))
            owner=user_db(db)
            active=db.query(Source).filter_by(owner_id=owner.id,enabled=True).filter(Source.access_status!='removed').count()
            failed=db.query(Source).filter_by(owner_id=owner.id,access_status='scan_error').count()
            latest=db.query(func.max(Source.last_checked_at)).filter_by(owner_id=owner.id).scalar()
        cfg=get_settings()
        t=(latest.strftime('%d.%m %H:%M UTC') if latest else 'пока не было')
        info=(f'🩺 <b>Диагностика PULSE</b>\nБаза: ✅ доступна\nYouTube API: {"✅ настроен" if cfg.youtube_api_key else "⚠️ нет ключа"}'
              f'\nTelegram: ✅ отвечает\nИсточники: {active}\nОшибок источников: {failed}\nПоследнее сканирование: {t}'
              '\nПанель браузера: выключена\nRedis: не требуется для работы бота')
    except Exception:
        logger.exception('Bot diagnostics failed')
        info='⚠️ База данных временно недоступна. Проверьте сеть сервера и PostgreSQL.'
    await screen(cb,info,key([[('⬅️ Настройки','settings')]]))


@extra_router.callback_query(F.data=='scan:all')
async def scan_all(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    await cb.answer('Запущена проверка источников')
    await cb.message.edit_text('🔄 Проверяю доступные YouTube и подключённые источники…',reply_markup=key([[('🏠 Главная','home')]]))
    try:
        result=await scan_all_once(force=True)
        info=f'✅ Проверено/обновлено роликов: {result["videos_checked"]}\nОшибок источников: {result["scan_errors"]}'
    except Exception:
        logger.exception('Manual scan failed')
        info='⚠️ Не удалось выполнить сканирование. Проверьте соединение с Neon и YouTube API.'
    await cb.message.answer(info,reply_markup=key([[('🏠 Главная','home')],[('🔥 Видео','videos')]]))


@extra_router.message(Command('status'))
async def status_command(message: Message):
    if not message.from_user or not owner_ok(message.from_user.id):return
    with SessionLocal() as db:
        u=user_db(db)
        sources=db.query(Source).filter_by(owner_id=u.id,enabled=True).filter(Source.access_status!='removed').count()
        vids=db.query(Video).filter_by(owner_id=u.id).count()
    await message.answer(f'⚡ PULSE\nИсточников: {sources}\nВидео: {vids}\nИспользуй /start для меню.')


@extra_router.message(F.video | F.animation | F.document)
async def own_upload(message: Message):
    """Personal upload is a Telegram message, no permanent server file."""
    if not message.from_user or not owner_ok(message.from_user.id):return
    media = message.video or message.animation or message.document
    if message.document and not (message.document.mime_type or '').startswith('video/'):
        return await message.answer('Этот файл не является видео. Отправьте MP4 или видео Telegram.')
    with SessionLocal() as db:
        u=user_db(db)
        name=f'owner-upload-{u.id}'
        src=db.query(Source).filter_by(owner_id=u.id,username=name).first()
        if not src:
            src=Source(owner_id=u.id,username=name,platform='telegram',
                       title='Мои загруженные файлы',access_status='owner_upload',
                       enabled=True,max_age_hours=None)
            db.add(src);db.flush()
        ingest(db,src,message.message_id,message.date,
               file_id=media.file_id, duration=getattr(media,'duration',None),
               width=getattr(media,'width',None),height=getattr(media,'height',None),
               caption=message.caption or '',views=None,reactions=None,replies=None)
    await message.answer('✅ Видео зарегистрировано. Файл хранится в Telegram; копию на сервер PULSE не скачивал.\n'
                         'Для дальнейшей публикации требуется подтвердить права в карточке источника.',
                         reply_markup=key([[('📡 Источники','sources')],[('🔥 Видео','videos')]]))


@extra_router.message(F.text, StateFilter(None))
async def youtube_link_without_wizard(message: Message, state: FSMContext):
    if not message.from_user or not owner_ok(message.from_user.id):return
    if parse_youtube_video_url(message.text or ''):
        return await save_source(message,state)
    await message.answer('Для управления PULSE используй /start.\nДля добавления канала нажми 📡 Источники.',
                         reply_markup=key([[('🏠 Главная','home')]]))

@extra_router.callback_query(F.data.startswith('account:'))
async def account_detail(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    try: account_id=int(cb.data.split(':')[1])
    except (IndexError, ValueError):return await cb.answer('Неверный аккаунт',show_alert=True)
    from .models import SocialAccount, AccountSchedule
    with SessionLocal() as db:
        u=user_db(db);a=db.query(SocialAccount).filter_by(id=account_id,owner_id=u.id).first()
        if not a:return await cb.answer('Аккаунт не найден',show_alert=True)
        sch=db.query(AccountSchedule).filter_by(owner_id=u.id,account_id=a.id).first()
        sched='⏸ Выкл.' if not sch or not sch.enabled else '✅ '+', '.join(sch.times)+' ('+sch.timezone+')'
        title=escape(a.username)
        active='✅ включён' if a.enabled else '⏸ отключён'
    await screen(cb,f'📱 <b>{a.provider} @{title}</b>\nСостояние: {active}\nРасписание: {escape(sched)}',
                 key([[('🗓 Расписание',f'schedule:{account_id}')],
                      [('⏸ Включить / выключить',f'accounttoggle:{account_id}')],
                      [('🗑 Отключить и стереть токены',f'accountremove:{account_id}')],
                      [('⬅️ Аккаунты','accounts')]]))


@extra_router.callback_query(F.data.startswith('accounttoggle:'))
async def toggle_account(cb:CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    from .models import SocialAccount
    aid=int(cb.data.split(':')[1])
    with SessionLocal() as db:
        u=user_db(db);a=db.query(SocialAccount).filter_by(owner_id=u.id,id=aid).first()
        if not a:return await cb.answer('Аккаунт не найден',show_alert=True)
        if a.provider != 'telegram' and not a.encrypted_access_token.strip():return await cb.answer('Для включения переподключите OAuth',show_alert=True)
        if not a.publishing_enabled:
            from .services.destinations import check_account
            if await check_account(db, a, cb.bot) != 'READY_TO_PUBLISH':
                return await cb.answer('??? ?????????????? ???? ??????????. ????????? ???????????.',show_alert=True)
        a.publishing_enabled=not a.publishing_enabled;db.commit()
    await account_detail(cb)


@extra_router.callback_query(F.data.startswith('accountremove:'))
async def ask_remove_account(cb:CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    aid=int(cb.data.split(':')[1])
    from .models import SocialAccount
    with SessionLocal() as db:
        u=user_db(db);a=db.query(SocialAccount).filter_by(id=aid,owner_id=u.id).first()
        if not a:return await cb.answer('Аккаунт не найден',show_alert=True)
        username=escape(a.username)
    await screen(cb,f'🗑 Удалить локальные OAuth-токены для @{username}?\nОтозвать доступ можно также в настройках самой соцсети.',
                 key([[('🗑 Да, отключить',f'accountremoveconfirm:{aid}')],[('Отмена',f'account:{aid}')]]))


@extra_router.callback_query(F.data.startswith('accountremoveconfirm:'))
async def remove_account(cb:CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    aid=int(cb.data.split(':')[1])
    from .models import SocialAccount, Route
    with SessionLocal() as db:
        u=user_db(db);a=db.query(SocialAccount).filter_by(id=aid,owner_id=u.id).first()
        if not a:return await cb.answer('Аккаунт не найден',show_alert=True)
        a.enabled=False;a.publishing_enabled=False;a.connection_status='DISCONNECTED';a.encrypted_access_token='';a.encrypted_refresh_token=None
        for r in db.query(Route).filter_by(owner_id=u.id,account_id=aid).all():r.enabled=False
        for j in db.query(Job).filter(Job.owner_id==u.id,Job.account_id==aid,Job.status.in_(['WAITING_APPROVAL','SCHEDULED'])).all():j.status='CANCELLED'
        db.commit()
    await screen(cb,'✅ Аккаунт отключён и токены удалены из PULSE. При необходимости отзовите разрешение также в Instagram/TikTok.',
                 key([[('📱 Аккаунты','accounts')]]))


@extra_router.callback_query(F.data.startswith('schedule:'))
async def schedule_account(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    aid=int(cb.data.split(':')[1])
    from .models import SocialAccount,AccountSchedule
    with SessionLocal() as db:
        u=user_db(db);a=db.query(SocialAccount).filter_by(owner_id=u.id,id=aid).first()
        if not a:return await cb.answer('Аккаунт не найден',show_alert=True)
        s=db.query(AccountSchedule).filter_by(owner_id=u.id,account_id=aid).first()
        display='Выключено — публикация без ежедневного расписания' if not s or not s.enabled else f'Включено: {", ".join(s.times)} / {s.timezone} / до {s.daily_max} в день'
    await screen(cb, f'🗓 <b>Расписание</b>\nАккаунт: @{escape(a.username)}\n{escape(display)}\nЧасовой пояс: Asia/Dushanbe\n'
                     'Выберите готовый набор. Времена можно изменить через этот раздел без компьютера.',
                 key([[('🕒 10:00 · 15:00 · 20:00',f'scheduleset:{aid}:3')],
                      [('🕙 10:00 ежедневно',f'scheduleset:{aid}:1')],
                      [('🕐 13:00 · 19:00',f'scheduleset:{aid}:2')],
                      [('✏️ Свои часы',f'schedulecustom:{aid}')],
                      [('⏸ Отключить',f'scheduleset:{aid}:0')],
                      [('⬅️ Аккаунт',f'account:{aid}')]]))


@extra_router.callback_query(F.data.startswith('scheduleset:'))
async def schedule_set(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    _,aid_raw,preset=cb.data.split(':')
    aid=int(aid_raw)
    if preset not in ('0','1','2','3'):return await cb.answer('Неизвестное расписание',show_alert=True)
    from .models import SocialAccount, AccountSchedule
    presets={'0':[], '1':['10:00'],'2':['13:00','19:00'],'3':['10:00','15:00','20:00']}
    with SessionLocal() as db:
        u=user_db(db);a=db.query(SocialAccount).filter_by(owner_id=u.id,id=aid).first()
        if not a:return await cb.answer('Аккаунт не найден',show_alert=True)
        s=db.query(AccountSchedule).filter_by(owner_id=u.id,account_id=aid).first()
        if not s:
            s=AccountSchedule(owner_id=u.id,account_id=aid)
            db.add(s)
        s.enabled=preset!='0';s.times=presets[preset] or ['10:00']
        s.daily_max=max(1,len(s.times));s.min_gap_minutes=120;s.timezone='Asia/Dushanbe'
        db.commit()
    await schedule_account(cb)


@extra_router.callback_query(F.data.startswith('routedisable:'))
async def route_disable(cb:CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    from .models import Route
    rid=int(cb.data.split(':')[1])
    with SessionLocal() as db:
        u=user_db(db);r=db.query(Route).filter_by(id=rid,owner_id=u.id).first()
        if not r:return await cb.answer('Маршрут не найден',show_alert=True)
        r.enabled=False;db.commit()
    await screen(cb,'✅ Маршрут отключён. Существующие задания не удалены.',key([[('🔀 Маршруты','routes')]]))

@extra_router.callback_query(F.data=='schedules')
async def schedules_menu(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    from .models import SocialAccount
    with SessionLocal() as db:
        u=user_db(db);items=db.query(SocialAccount).filter_by(owner_id=u.id,enabled=True).all()
        rows=[[(f'🗓 {a.provider}: @{a.username}'[:55],f'schedule:{a.id}')] for a in items]
    if not rows:
        return await screen(cb,'🗓 Нет подключённых аккаунтов. Мониторинг YouTube при этом работает без аккаунтов Instagram/TikTok.',
                            key([[('⬅️ Главная','home')]]))
    await screen(cb,'🗓 Выберите аккаунт, чтобы настроить расписание.',key(rows+[[('⬅️ Главная','home')]]))

@extra_router.callback_query(F.data.startswith('sendmedia:'))
async def send_telegram_file(cb:CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    vid=int(cb.data.split(':')[1])
    with SessionLocal() as db:
        u=user_db(db);v=db.query(Video).filter_by(id=vid,owner_id=u.id).first()
        if not v or not v.telegram_file_id:return await cb.answer('Файл отсутствует в Telegram. YouTube предоставляет только ссылку.',show_alert=True)
        file_id=v.telegram_file_id
    await cb.answer('Отправляю видео')
    try:
        await cb.message.answer_video(video=file_id,caption=f'⚡ PULSE · видео #{vid}')
    except Exception:
        try:
            await cb.message.answer_document(document=file_id,caption=f'⚡ PULSE · файл #{vid}')
        except Exception:
            logger.exception('Telegram file send failed video=%s',vid)
            await cb.message.answer('⚠️ Telegram не может повторно отправить этот файл. Он мог стать недоступен.')


@extra_router.callback_query(F.data.startswith('schedulecustom:'))
async def ask_custom_times(cb:CallbackQuery,state:FSMContext):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    aid=int(cb.data.split(':')[1])
    from .models import SocialAccount
    with SessionLocal() as db:
        u=user_db(db)
        if not db.query(SocialAccount).filter_by(owner_id=u.id,id=aid,enabled=True).first():
            return await cb.answer('Аккаунт не найден',show_alert=True)
    await state.set_state(Wizard.schedule_times)
    await state.update_data(account_id=aid)
    await screen(cb,'🕒 Отправьте время публикаций через запятую, например:\n10:00, 15:00, 20:30\n'
                    'До 10 времён в день, часовой пояс Asia/Dushanbe. /cancel — отмена.',
                 key([[('⬅️ Главная','home')]]))


@extra_router.message(StateFilter(Wizard.schedule_times))
async def save_custom_times(message:Message,state:FSMContext):
    if not message.from_user or not owner_ok(message.from_user.id):return
    import re
    times=[t.strip() for t in (message.text or '').split(',')]
    if not 1<=len(times)<=10 or len(set(times))!=len(times) or not all(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',t) for t in times):
        return await message.answer('Ошибка. Используйте HH:MM, HH:MM (до 10 неповторяющихся времён).')
    from .models import SocialAccount,AccountSchedule
    data=await state.get_data()
    with SessionLocal() as db:
        u=user_db(db);a=db.query(SocialAccount).filter_by(id=data.get('account_id'),owner_id=u.id,enabled=True).first()
        if not a:return await message.answer('Аккаунт недоступен. /cancel')
        s=db.query(AccountSchedule).filter_by(owner_id=u.id,account_id=a.id).first()
        if not s:
            s=AccountSchedule(owner_id=u.id,account_id=a.id)
            db.add(s)
        s.enabled=True;s.times=sorted(times);s.daily_max=len(times);s.min_gap_minutes=120;s.timezone='Asia/Dushanbe'
        db.commit()
    await state.clear()
    await message.answer('✅ Расписание обновлено. Дни: понедельник–воскресенье.')
    await home(message)

@extra_router.callback_query(F.data.startswith('srcpage:'))
async def sources_navigation(cb:CallbackQuery):
    from .bot import sources_page
    try:page=int(cb.data.split(':')[1])
    except (IndexError,ValueError):return await cb.answer('Ошибка страницы',show_alert=True)
    await sources_page(cb,page)


@extra_router.callback_query(F.data.startswith('vidpage:'))
async def videos_navigation(cb:CallbackQuery):
    from .bot import videos_page
    try:page=int(cb.data.split(':')[1])
    except (IndexError,ValueError):return await cb.answer('Ошибка страницы',show_alert=True)
    await videos_page(cb,page)


def safe_csv_value(value:object) -> object:
    """Prevent spreadsheet formula injection when opened in Excel/Sheets."""
    if value is None:return ''
    value=str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value


@extra_router.callback_query(F.data=='analytics:csv')
async def analytics_csv(cb:CallbackQuery):
    if not owner_ok(cb.from_user.id):return await cb.answer('Недостаточно прав',show_alert=True)
    import csv
    import io
    from aiogram.types import BufferedInputFile
    await cb.answer('Готовлю CSV')
    stream=io.StringIO(newline='')
    writer=csv.writer(stream)
    writer.writerow(['id','source_platform','video_id','source_url','published_utc',
                     'views','reactions_likes','comments','duration_seconds','eligible','filter_reason'])
    with SessionLocal() as db:
        u=user_db(db)
        # Hard cap protects free databases and bot memory; report is recent first.
        videos=db.query(Video).filter_by(owner_id=u.id).order_by(Video.id.desc()).limit(10000).all()
        source_ids={v.source_id for v in videos}
        sources={s.id:s.platform for s in db.query(Source).filter(Source.owner_id==u.id,Source.id.in_(source_ids)).all()}
        for v in videos:
            writer.writerow([v.id,sources.get(v.source_id,''),v.remote_video_id or v.message_id,
                             safe_csv_value(v.source_url),v.published_at.isoformat() if v.published_at else '',
                             v.views if v.views is not None else '',
                             v.reactions if v.reactions is not None else '',
                             v.replies if v.replies is not None else '',
                             v.duration if v.duration is not None else '',v.eligible,
                             safe_csv_value(v.filter_reason)])
    payload=('\ufeff'+stream.getvalue()).encode('utf-8')
    await cb.message.answer_document(BufferedInputFile(payload,filename='pulse-videos.csv'),
                                     caption=f'📊 PULSE · CSV · {len(videos)} последних видео')
