import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import TELEGRAM_BOT_TOKEN
from app.core.state import GlobalState
from app.core.database import initialize_database
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

def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    
    # Include Routers
    dp.include_router(common.router)
    dp.include_router(admin.router)
    dp.include_router(schedule.router)
    dp.include_router(teachers.router)
    dp.include_router(session.router)
    dp.include_router(rating.router)
    dp.include_router(subject_rating.router)
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
    scheduler.add_job(periodic_update, 'interval', hours=6, args=[bot]) # Example: every 6 hours
    
    # Расписание фоновой проверки сессии (например, каждые 4 часа)
    scheduler.add_job(run_session_tracking, 'interval', hours=4, args=[bot])
    
    # Обновление рейтинга раз в сутки (в 2:00 ночи)
    from app.services.rating_updater import run_rating_update
    scheduler.add_job(run_rating_update, 'cron', hour=2, minute=0, args=[bot])
    
    from app.services.backup import send_db_backup
    scheduler.add_job(send_db_backup, 'cron', hour=20, minute=0, args=[bot])
    
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

if __name__ == "__main__":
    from app.core.logger import setup_logging
    setup_logging()
    asyncio.run(start_bot())
