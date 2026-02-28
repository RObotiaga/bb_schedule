import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import TELEGRAM_BOT_TOKEN
from app.core.state import GlobalState
from app.core.database import initialize_database
from app.services.schedule_sync import run_full_sync
from app.bot.handlers import common, schedule, teachers, session, admin

async def periodic_update():
    logging.info("⏳ Запуск периодического обновления расписания...")
    success = await run_full_sync()
    if success:
        await GlobalState.reload()
        logging.info("✅ Периодическое обновление завершено успешно.")
    else:
        logging.error("❌ Периодическое обновление завершилось ошибкой.")

def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    
    # Include Routers
    dp.include_router(common.router)
    dp.include_router(schedule.router)
    dp.include_router(teachers.router)
    dp.include_router(session.router)
    dp.include_router(admin.router)
    return dp

async def start_bot():
    logging.info("Starting Bot...")
    
    # Init DB
    await initialize_database()
    await GlobalState.reload()
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = create_dispatcher()
    
    from app.services.session_tracker import run_session_tracking
    
    # Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(periodic_update, 'interval', hours=6) # Example: every 6 hours
    
    # Расписание фоновой проверки сессии (например, каждые 4 часа)
    scheduler.add_job(run_session_tracking, 'interval', hours=4, args=[bot])
    
    scheduler.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    from app.core.logger import setup_logging
    setup_logging()
    asyncio.run(start_bot())
