"""Headless production runtime: Telegram Bot + scan loop + publication worker.

Only one runtime instance should poll this bot token. Deploy as a background
worker/VPS service, never as an auto-suspending HTTP free-tier website.
"""
import asyncio
import logging
import sys
import signal
import time
from .bot import run as telegram_polling
from .services.source_monitor import run as source_monitor
from .services.worker import run as publication_worker
from .config import get_settings

logger=logging.getLogger(__name__)


async def heartbeat():
    root = get_settings().storage_dir
    root.mkdir(parents=True, exist_ok=True)
    while True:
        (root / 'runtime-heartbeat').write_text(str(time.time()), encoding='ascii')
        await asyncio.sleep(15)


async def run():
    cfg=get_settings()
    if not cfg.bot_only:
        raise RuntimeError('BOT_ONLY=true required for Telegram runtime')
    if not cfg.database_url.startswith('postgresql+psycopg://'):
        raise RuntimeError('Remote PostgreSQL DATABASE_URL required')
    runners={
        'telegram-bot': asyncio.create_task(telegram_polling(),name='telegram-bot'),
        'source-monitor': asyncio.create_task(source_monitor(),name='source-monitor'),
        'publication-worker': asyncio.create_task(publication_worker(),name='publication-worker'),
        'heartbeat': asyncio.create_task(heartbeat(),name='heartbeat'),
    }
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, stop.set)
        except NotImplementedError: signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    stop_task = asyncio.create_task(stop.wait(), name='shutdown')
    try:
        completed, pending=await asyncio.wait([*runners.values(), stop_task],return_when=asyncio.FIRST_COMPLETED)
        if stop_task in completed: return
        for task in completed:
            if task.cancelled():
                logger.error('Critical task cancelled: %s',task.get_name())
            elif task.exception():
                logger.error('Critical task failed: %s',task.get_name(),exc_info=task.exception())
            else:
                logger.error('Critical task terminated unexpectedly: %s',task.get_name())
        # Non-zero exit so Docker/service supervisor can restart it.
        raise RuntimeError('A critical PULSE runtime component stopped')
    finally:
        stop_task.cancel()
        for task in runners.values():
            if not task.done():task.cancel()
        await asyncio.gather(*runners.values(),stop_task,return_exceptions=True)


def main():
    logging.getLogger('httpx').setLevel(logging.WARNING)  # Do not log YouTube API key in request URL
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__=='__main__':
    main()
