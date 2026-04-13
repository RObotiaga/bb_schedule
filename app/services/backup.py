from aiogram import Bot
from aiogram.types import FSInputFile
from app.core.config import DB_PATH, ADMIN_ID
import logging
from datetime import datetime
import os

async def send_db_backup(bot: Bot):
    if not ADMIN_ID:
        logging.warning("ADMIN_ID не установлен, бэкап БД отменен.")
        return
    
    if not os.path.exists(DB_PATH):
        logging.error(f"Файл БД не существует по пути: {DB_PATH}")
        return

    try:
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        doc = FSInputFile(DB_PATH, filename="schedule_backup.db")
        await bot.send_document(
            chat_id=ADMIN_ID,
            document=doc,
            caption=f"📂 *Ежедневный бэкап базы данных*\n\nДата: {now_str}",
            parse_mode="Markdown"
        )
        logging.info("Ежедневный бэкап успешно отправлен админу.")
    except Exception as e:
        logging.exception("Ошибка при отправке бэкапа БД")
