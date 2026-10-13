"""Owner-only account manager, native chat picker and publication confirmation."""
import secrets
from html import escape
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (CallbackQuery, Message, ChatMemberUpdated, KeyboardButton,
                           KeyboardButtonRequestChat, ReplyKeyboardMarkup, ReplyKeyboardRemove,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.exceptions import TelegramAPIError
from .bot import owner_ok, user_db, screen, key
from .db import SessionLocal
from .models import SocialAccount, TelegramDestinationChat, Job, AccountSchedule, Route, Source
from .services.destinations import connect_telegram, check_account, enqueue_destinations, DestinationError

accounts_router = Router()


@accounts_router.message(Command('cancel', 'start'))
async def reset_dialog(message: Message, state: FSMContext):
    if not message.from_user or not owner_ok(message.from_user.id): return
    await state.clear()
    if message.chat.type == 'private':
        await message.answer('Ввод завершён', reply_markup=ReplyKeyboardRemove())
    from .bot import start
    await start(message)


class AccountWizard(StatesGroup):
    pick_chat = State()
    caption = State()
    schedule = State()


@accounts_router.callback_query(F.data.startswith('oauthhelp:'))
async def oauth_help(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    provider = cb.data.split(':')[1]
    if provider not in ('instagram', 'tiktok'): return await cb.answer('Неизвестная платформа')
    portal = 'https://developers.facebook.com/apps/' if provider == 'instagram' else 'https://developers.tiktok.com/'
    product = 'Instagram API with Instagram Login' if provider == 'instagram' else 'Login Kit (Web) и Content Posting API (Upload)'
    text = (f'📖 Первоначальная настройка {provider.title()}\n\n'
            f'1. В developer-консоли зарегистрируйте приложение и продукт {product}.\n'
            '2. Настройте публичный HTTPS-домен, ведущий на OAuth callback PULSE. localhost/127.0.0.1 не подходят.\n'
            f'3. Зарегистрируйте redirect URI: https://ВАШ-ДОМЕН/oauth/{provider}/callback.\n'
            '4. Получите App ID/Secret (Meta) или Client key/secret (TikTok).\n'
            '5. В корне проекта: python scripts/pulse_local.py oauth-setup. Секреты вводятся скрыто локально.\n'
            '6. Перезапустите PULSE, затем повторите подключение.\n\n'
            'Подробная инструкция: docs/OAUTH_FIRST_SETUP.md. Если платформа требует Review, его проходит владелец приложения.')
    await screen(cb, text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Developer-консоль', url=portal)],
        [InlineKeyboardButton(text='Повторить подключение', callback_data=f'oauth:{provider}')],
        [InlineKeyboardButton(text='⬅️ Аккаунты', callback_data='accounts')]]))


@accounts_router.callback_query(F.data.startswith('priority:'))
async def priority(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    aid = int(cb.data.split(':')[1])
    with SessionLocal() as db:
        a = db.query(SocialAccount).filter_by(id=aid, owner_id=user_db(db).id).first()
        if not a: return await cb.answer('Направление не найдено')
        value = (int((a.settings or {}).get('priority', 0)) + 10) % 30
        a.settings = {**(a.settings or {}), 'priority': value}; db.commit()
    await screen(cb, f'Приоритет новых заданий: {value} (0 — обычный, 10 — повышенный, 20 — высокий)', key([[('⬅️ Назад', f'account:{aid}')]]))


@accounts_router.callback_query(F.data.startswith('routemode:'))
async def route_mode(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    rid = int(cb.data.split(':')[1])
    with SessionLocal() as db:
        u = user_db(db); r = db.query(Route).filter_by(owner_id=u.id, id=rid).first()
        if not r: return await cb.answer('Маршрут не найден')
        src = db.get(Source, r.source_id); account = db.get(SocialAccount, r.account_id)
        allowed = ['MANUAL'] if src.platform != 'telegram' else (['DRAFT', 'MANUAL'] if account.provider == 'tiktok' else ['APPROVAL', 'AUTO', 'MANUAL'])
        mode = allowed[(allowed.index(r.mode) + 1) % len(allowed)] if r.mode in allowed else allowed[0]
        r.mode = mode; r.enabled = True; db.commit()
    await screen(cb, 'Режим новых заданий: ' + mode + '\nAUTO использует подтверждённые права и включённые публикации направления.', key([[('🔀 Маршруты', 'routes')]]))


@accounts_router.callback_query(F.data.startswith('documentmode:'))
async def document_mode(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    aid = int(cb.data.split(':')[1])
    with SessionLocal() as db:
        a = db.query(SocialAccount).filter_by(id=aid, owner_id=user_db(db).id, provider='telegram').first()
        if not a: return await cb.answer('Направление недоступно', show_alert=True)
        mode = 'video' if (a.settings or {}).get('media_kind') == 'document' else 'document'
        a.settings = {**(a.settings or {}), 'media_kind': mode}; db.commit()
    await screen(cb, 'Формат отправки: ' + mode, key([[('⬅️ Направление', f'account:{aid}')]]))


@accounts_router.callback_query(F.data.startswith('accthistory:'))
async def account_history(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    aid = int(cb.data.split(':')[1])
    with SessionLocal() as db:
        u = user_db(db)
        jobs = db.query(Job).filter_by(owner_id=u.id, account_id=aid).order_by(Job.id.desc()).limit(20).all()
        lines = [f'#{j.id}: {j.status}' for j in jobs]
    await screen(cb, '\n'.join(lines) or 'История пуста', key([[('⬅️ Направление', f'account:{aid}')]]))


@accounts_router.callback_query(F.data.startswith('scheduleadvanced:'))
async def advanced_schedule(cb: CallbackQuery, state: FSMContext):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    await state.set_state(AccountWizard.schedule)
    await state.update_data(account_id=int(cb.data.split(':')[1]))
    await screen(cb, 'Введите: часовой пояс; дни 1–7; время; максимум в день; интервал в минутах.\n'
                 'Пример: Asia/Dushanbe; 1,2,3,4,5; 10:00,16:00; 2; 120\n/cancel', key([[('Отмена', 'home')]]))


@accounts_router.message(AccountWizard.schedule)
async def save_advanced_schedule(message: Message, state: FSMContext):
    if not message.from_user or not owner_ok(message.from_user.id): return
    import re
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        zone, days, times, maximum, gap = [v.strip() for v in (message.text or '').split(';')]
        ZoneInfo(zone)
        days = sorted(set(int(v.strip()) - 1 for v in days.split(',')))
        times = sorted(set(v.strip() for v in times.split(',')))
        maximum, gap = int(maximum), int(gap)
        if not days or not all(0 <= d <= 6 for d in days) or not 1 <= len(times) <= 10:
            raise ValueError
        if not all(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in times) or not 1 <= maximum <= 10 or not 0 <= gap <= 1440:
            raise ValueError
    except (ValueError, ZoneInfoNotFoundError):
        return await message.answer('Неверный формат. Проверьте часовой пояс, дни, HH:MM, лимит 1–10 и интервал 0–1440.')
    data = await state.get_data()
    with SessionLocal() as db:
        u = user_db(db); a = db.query(SocialAccount).filter_by(owner_id=u.id, id=data['account_id']).first()
        if not a: return await message.answer('Направление не найдено. /cancel')
        schedule = db.query(AccountSchedule).filter_by(owner_id=u.id, account_id=a.id).first()
        if not schedule:
            schedule = AccountSchedule(owner_id=u.id, account_id=a.id); db.add(schedule)
        schedule.timezone = zone; schedule.days = days; schedule.times = times
        schedule.daily_max = maximum; schedule.min_gap_minutes = gap; schedule.enabled = True
        db.commit()
    await state.clear(); await message.answer('✅ Индивидуальное расписание сохранено.')


@accounts_router.callback_query(F.data.startswith('accts:'))
async def accounts_page(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id):
        return await cb.answer('Доступ запрещён', show_alert=True)
    _, provider, page = cb.data.split(':')
    if provider not in ('all', 'instagram', 'tiktok', 'telegram'):
        return await cb.answer('Неверная платформа')
    page = max(0, int(page))
    with SessionLocal() as db:
        u = user_db(db)
        q = db.query(SocialAccount).filter_by(owner_id=u.id)
        counts = {p: q.filter_by(provider=p, enabled=True).count() for p in ('instagram', 'tiktok', 'telegram')}
        if provider != 'all': q = q.filter_by(provider=provider)
        total = q.count(); page = min(page, max(0, (total - 1) // 8))
        accounts = q.order_by(SocialAccount.id).offset(page * 8).limit(8).all()
    lines = ['📱 <b>МОИ АККАУНТЫ</b>'] + [f'{p}: {n} подключено' for p, n in counts.items()]
    lines.append(f'Всего направлений: {sum(counts.values())}')
    rows = [[('📸 Instagram', 'accts:instagram:0'), ('🎵 TikTok', 'accts:tiktok:0')], [('✈️ Telegram', 'accts:telegram:0')]]
    rows += [[(f'{a.provider}: {a.username}'[:48], f'account:{a.id}')] for a in accounts]
    rows += [[('➕ Instagram', 'oauth:instagram'), ('➕ TikTok', 'oauth:tiktok')],
             [('➕ Канал', 'tgadd:channel'), ('➕ Группа', 'tgadd:group')],
             [('🔄 Проверить подключения', 'accountcheck:all'), ('🔀 Маршруты', 'routes')]]
    if page: rows.append([('◀️', f'accts:{provider}:{page-1}')])
    if (page + 1) * 8 < total: rows.append([('▶️', f'accts:{provider}:{page+1}')])
    rows.append([('🏠 Главное меню', 'home')])
    await screen(cb, '\n'.join(lines), key(rows))


@accounts_router.callback_query(F.data.startswith('tgadd:'))
async def add_chat(cb: CallbackQuery, state: FSMContext):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    if cb.message.chat.type != 'private': return await cb.answer('Откройте личный чат PULSE', show_alert=True)
    channel = cb.data.endswith('channel')
    request_id = secrets.randbelow(2**30) + 1
    await state.set_state(AccountWizard.pick_chat)
    await state.update_data(request_id=request_id)
    await cb.answer()
    me = await cb.bot.get_me()
    link = (f'https://t.me/{me.username}?startchannel&admin=post_messages' if channel else
            f'https://t.me/{me.username}?startgroup=pulse')
    await cb.message.answer('Добавить PULSE в чат:', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='➕ Добавить PULSE', url=link)]]))
    await cb.message.answer('Добавьте PULSE в чат. Для канала: администратор с правом публикации. '
                            'Для группы: разрешите сообщения и видео. Затем выберите чат кнопкой ниже. /cancel — отмена.',
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[[
            KeyboardButton(text='Выбрать канал' if channel else 'Выбрать группу',
                request_chat=KeyboardButtonRequestChat(request_id=request_id, chat_is_channel=channel,
                    chat_is_created=True, bot_is_member=True, request_title=True, request_username=True))]]))


@accounts_router.message(AccountWizard.pick_chat, F.chat_shared)
async def shared_chat(message: Message, state: FSMContext):
    if not message.from_user or not owner_ok(message.from_user.id) or message.chat.type != 'private': return
    data = await state.get_data()
    if data.get('request_id') != message.chat_shared.request_id:
        return await message.answer('Выбор устарел. /cancel и попробуйте снова.')
    try:
        with SessionLocal() as db:
            a = await connect_telegram(db, message.bot, user_db(db), message.chat_shared.chat_id)
            aid = a.id
    except (DestinationError, TelegramAPIError):
        return await message.answer('Чат недоступен или нет прав. Добавьте бота и проверьте права администратора. /cancel')
    await state.clear()
    await message.answer('✅ Направление подключено. Публикации выключены до вашего подтверждения.', reply_markup=ReplyKeyboardRemove())
    await message.answer('Откройте настройки:', reply_markup=key([[('⚙️ Направление', f'account:{aid}')]]))


@accounts_router.message(Command('pulse_connect'))
async def connect_topic(message: Message):
    if not message.from_user or not owner_ok(message.from_user.id): return
    if message.chat.type not in ('group', 'supergroup'): return
    try:
        with SessionLocal() as db:
            a = await connect_telegram(db, message.bot, user_db(db), message.chat.id, message.message_thread_id)
            aid = a.id
        await message.bot.send_message(message.from_user.id, '✅ Группа / тема подключена. Публикации выключены.',
            reply_markup=key([[('⚙️ Настроить', f'account:{aid}')]]))
    except (DestinationError, TelegramAPIError):
        await message.answer('Не удалось подтвердить ваши права или права PULSE. Проверьте настройки чата.')


@accounts_router.my_chat_member()
async def membership(update: ChatMemberUpdated):
    with SessionLocal() as db:
        targets = db.query(TelegramDestinationChat).filter_by(chat_id=update.chat.id).all()
        for target in targets:
            account = db.get(SocialAccount, target.account_id)
            await check_account(db, account, update.bot)
    if owner_ok(update.from_user.id) and update.new_chat_member.status in ('administrator', 'member'):
        await update.bot.send_message(update.from_user.id, 'PULSE добавлен. Откройте «Мои аккаунты» → добавить канал/группу. '
                                      'Для темы форума отправьте /pulse_connect внутри нужной темы.')


@accounts_router.message(F.migrate_to_chat_id)
async def chat_migrated(message: Message):
    with SessionLocal() as db:
        for target in db.query(TelegramDestinationChat).filter_by(chat_id=message.chat.id).all():
            target.chat_id = message.migrate_to_chat_id; target.chat_type = 'supergroup'
            account = db.get(SocialAccount, target.account_id)
            account.remote_id = str(target.chat_id) + (f':{target.message_thread_id}' if target.message_thread_id else '')
        db.commit()


@accounts_router.callback_query(F.data.startswith('accountcheck:'))
async def check_connections(cb: CallbackQuery):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    await cb.answer('Проверяю доступ; сообщений в направлениях не отправляю')
    aid = cb.data.split(':')[1]
    with SessionLocal() as db:
        u = user_db(db); q = db.query(SocialAccount).filter_by(owner_id=u.id, enabled=True)
        if aid != 'all': q = q.filter_by(id=int(aid))
        accounts = q.limit(100).all()
        lines = []
        for a in accounts:
            status = await check_account(db, a, cb.bot)
            lines.append(f'{escape(a.username)}: {status}')
    await screen(cb, '\n'.join(lines) or 'Подключений нет', key([[('⬅️ Аккаунты', 'accounts')]]))


@accounts_router.callback_query(F.data.startswith('caption:'))
async def caption_start(cb: CallbackQuery, state: FSMContext):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    await state.set_state(AccountWizard.caption)
    await state.update_data(account_id=int(cb.data.split(':')[1]))
    await screen(cb, 'Пришлите шаблон описания (до 2200 символов). Подстановки: {caption}, {source}. /cancel', key([[('Отмена', 'home')]]))


@accounts_router.message(AccountWizard.caption)
async def caption_save(message: Message, state: FSMContext):
    if not message.from_user or not owner_ok(message.from_user.id): return
    text = message.text or ''
    if not 1 <= len(text) <= 2200: return await message.answer('Нужно от 1 до 2200 символов.')
    data = await state.get_data()
    with SessionLocal() as db:
        u = user_db(db); a = db.query(SocialAccount).filter_by(id=data['account_id'], owner_id=u.id).first()
        if not a: return await message.answer('Направление не найдено. /cancel')
        a.settings = {**(a.settings or {}), 'caption_template': text}; db.commit()
    await state.clear(); await message.answer('✅ Шаблон сохранён.')


@accounts_router.callback_query(F.data.startswith('publish:'))
async def publish_picker(cb: CallbackQuery, state: FSMContext):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    vid = int(cb.data.split(':')[1])
    await state.clear(); await state.update_data(video_id=vid, destinations=[])
    await render_picker(cb, state)


async def render_picker(cb, state):
    data = await state.get_data(); selected = data.get('destinations', [])
    with SessionLocal() as db:
        u = user_db(db)
        accounts = db.query(SocialAccount).filter_by(owner_id=u.id, enabled=True, publishing_enabled=True).limit(50).all()
    rows = [[(('✅ ' if a.id in selected else '☐ ') + f'{a.provider}: {a.username}'[:45], f'pubpick:{a.id}')] for a in accounts]
    rows += [[('✅ По расписанию', 'pubconfirm'), ('🚀 Сейчас', 'pubconfirm:now')],
             [('⏳ На подтверждение', 'pubconfirm:approval')], [('Отмена', 'home')]]
    await screen(cb, 'Выберите направления. Подтверждение создаст отдельные задания по их расписаниям. '
                 'Вы подтверждаете право размещать исходное видео. TikTok: только загрузка в черновики; '
                 'публикацию завершаете в TikTok.', key(rows))


@accounts_router.callback_query(F.data.startswith('pubpick:'))
async def publish_select(cb: CallbackQuery, state: FSMContext):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    data = await state.get_data(); aid = int(cb.data.split(':')[1]); selected = data.get('destinations', [])
    if aid in selected: selected.remove(aid)
    else: selected.append(aid)
    await state.update_data(destinations=selected); await render_picker(cb, state)


@accounts_router.callback_query((F.data == 'pubconfirm') | F.data.startswith('pubconfirm:'))
async def publish_confirm(cb: CallbackQuery, state: FSMContext):
    if not owner_ok(cb.from_user.id): return await cb.answer('Доступ запрещён', show_alert=True)
    data = await state.get_data()
    if not data.get('video_id'): return await cb.answer('Выбор устарел', show_alert=True)
    with SessionLocal() as db:
        timing = cb.data.split(':')[1] if ':' in cb.data else 'schedule'
        try: jobs = enqueue_destinations(db, user_db(db).id, data['video_id'], data.get('destinations', []), timing)
        except DestinationError as exc: return await cb.answer(str(exc), show_alert=True)
        ids = ', '.join(f'#{j.id}' for j in jobs)
    await state.clear()
    await screen(cb, '✅ Задания: ' + ids, key([[('📤 Очередь', 'jobs')]]))
