import asyncio
import inspect
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import TELEGRAM_BOT_TOKEN
from app.core.state import GlobalState
from app.core.database import initialize_database, close_db_connection
from app.services.schedule_sync import run_full_sync
from app.bot.handlers import common, schedule, teachers, session, admin, rating, subject_rating

async def periodic_update(bot: Bot):
    logging.info("⏳ Запуск периодического обновления расписания...")
    from app.core.config import ADMIN_ID
    success = await run_full_sync()
    if success:
        await GlobalState.reload()
        logging.info("✅ Периодическое обновление завершено успешно.")
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID, 
                    "✅ *Автоматическое обновление расписания*\n\nРасписание успешно загружено и обновлено.", 
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление: {e}")
    else:
        logging.error("❌ Периодическое обновление завершилось ошибкой.")
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID, 
                    "❌ *Ошибка авто-обновления*\n\nПроизошла ошибка при фоновом обновлении расписания. Проверьте логи.", 
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление: {e}")

async def _shutdown_dispatcher_handlers(router: Dispatcher, **_):
    """Отменяет и дожидается активных aiogram update-handler задач.

    aiogram 3.31.0 по умолчанию запускает каждый update как отдельную Task
    и хранит их в ``Dispatcher._handle_update_tasks``. Shutdown hook
    вызывается после остановки polling-loop, но до закрытия bot.session,
    поэтому это последняя безопасная точка, чтобы обработчики не смогли
    повторно открыть общую БД после финального close.
    """
    tasks = [
        task
        for task in list(router._handle_update_tasks)
        if not task.done() and task is not asyncio.current_task()
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.shutdown.register(_shutdown_dispatcher_handlers)
    
    # Include Routers
    dp.include_router(common.router)
    dp.include_router(admin.router)
    dp.include_router(schedule.router)
    dp.include_router(teachers.router)
    dp.include_router(session.router)
    dp.include_router(rating.router)
    dp.include_router(subject_rating.router)
    return dp


async def _tracked_scheduler_job(
    active_tasks: set[asyncio.Task],
    stopping: asyncio.Event,
    func,
    *args,
):
    """Запускает scheduler job под контролем shutdown бота.

    APScheduler 3.x не умеет по-настоящему ждать coroutine jobs через
    AsyncIOScheduler.shutdown(wait=True), поэтому реальные asyncio.Task
    отслеживаются отдельно и завершаются до закрытия общей БД.
    """
    if stopping.is_set():
        return None

    task = asyncio.current_task()
    if task is not None:
        active_tasks.add(task)
    try:
        result = func(*args)
        if inspect.isawaitable(result):
            return await result
        return result
    finally:
        if task is not None:
            active_tasks.discard(task)


async def _shutdown_scheduler(
    scheduler: AsyncIOScheduler | None,
    active_tasks: set[asyncio.Task],
    stopping: asyncio.Event,
):
    """Останавливает новые scheduler jobs и завершает уже запущенные."""
    stopping.set()

    if scheduler is not None and scheduler.running:
        scheduler.pause()

    tasks = [task for task in list(active_tasks) if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    if scheduler is not None and scheduler.running:
        # AsyncIOScheduler 3.x планирует shutdown через call_soon_threadsafe.
        # Один yield позволяет callback завершить dismantle executor'а до БД.
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)

async def start_bot():
    logging.info("Starting Bot...")

    scheduler: AsyncIOScheduler | None = None
    scheduled_tasks: set[asyncio.Task] = set()
    scheduler_stopping = asyncio.Event()

    # Перед финальным закрытием БД обязательно останавливаем все фоновые jobs:
    # ни одна задача не должна повторно открыть соединение после close.
    try:
        await initialize_database()
        await GlobalState.reload()

        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        dp = create_dispatcher()

        from app.services.session_tracker import run_session_tracking

        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            _tracked_scheduler_job,
            'interval',
            hours=6,
            args=[scheduled_tasks, scheduler_stopping, periodic_update, bot],
        )
        scheduler.add_job(
            _tracked_scheduler_job,
            'interval',
            hours=4,
            args=[scheduled_tasks, scheduler_stopping, run_session_tracking, bot],
        )

        from app.services.rating_updater import run_rating_update
        scheduler.add_job(
            _tracked_scheduler_job,
            'cron',
            hour=2,
            minute=0,
            args=[scheduled_tasks, scheduler_stopping, run_rating_update, bot],
        )

        from app.services.backup import send_db_backup
        scheduler.add_job(
            _tracked_scheduler_job,
            'cron',
            hour=20,
            minute=0,
            args=[scheduled_tasks, scheduler_stopping, send_db_backup, bot],
        )

        scheduler.start()

        from aiogram.types import BotCommand
        commands = [
            BotCommand(command="start", description="Перезапустить бота"),
            BotCommand(command="top", description="Рейтинг успеваемости"),
            BotCommand(command="top_subjects", description="Рейтинг преподавателей по предметам"),
        ]
        await bot.set_my_commands(commands)

        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        try:
            await _shutdown_scheduler(scheduler, scheduled_tasks, scheduler_stopping)
        finally:
            await close_db_connection()

if __name__ == "__main__":
    from app.core.logger import setup_logging
    setup_logging()
    asyncio.run(start_bot())
