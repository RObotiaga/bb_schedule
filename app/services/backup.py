from aiogram import Bot
from aiogram.types import FSInputFile
from app.core.config import ADMIN_ID
from app.core.database import create_database_snapshot_file
import logging
from datetime import datetime
import os


async def send_db_backup(bot: Bot):
    """Отправляет согласованный snapshot SQLite, включая данные из WAL."""
    if not ADMIN_ID:
        logging.warning("ADMIN_ID не установлен, бэкап БД отменен.")
        return

    snapshot_path = None
    try:
        snapshot_path = await create_database_snapshot_file()
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        doc = FSInputFile(snapshot_path, filename="schedule_backup.db")
        await bot.send_document(
            chat_id=ADMIN_ID,
            document=doc,
            caption=f"📂 *Ежедневный бэкап базы данных*\n\nДата: {now_str}",
            parse_mode="Markdown",
        )
        logging.info("Ежедневный согласованный бэкап успешно отправлен админу.")
    except Exception:
        logging.exception("Ошибка при отправке бэкапа БД")
    finally:
        if snapshot_path:
            try:
                os.remove(snapshot_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logging.warning("Не удалось удалить временный backup %s: %s", snapshot_path, exc)
