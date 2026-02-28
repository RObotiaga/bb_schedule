import asyncio
import logging
from aiogram import Bot

from app.core.database import get_users_with_record_books, get_cached_session_results, save_cached_session_results
from app.services.schedule_api import UsurtScraper

def compare_session_results(old_data: list, new_data: list) -> list[str]:
    notifications = []
    
    if not old_data or not new_data:
        return notifications
        
    old_dict = {f"{item.get('semester', '')}_{item['subject']}": item for item in old_data}
    new_dict = {f"{item.get('semester', '')}_{item['subject']}": item for item in new_data}
    
    for key, new_item in new_dict.items():
        if key not in old_dict:
            # Новый предмет
            icon = "✅" if new_item['passed'] else "❌"
            if "неудовл" in new_item['grade'].lower(): icon = "❌"
            
            notifications.append(
                f"🆕 *Новый результат!*\n"
                f"🎓 {new_item.get('course', 'Неизвестный курс')} | 📅 {new_item.get('semester', 'Семестр')}\n"
                f"{icon} *{new_item['subject']}*\n"
                f"🔹 {new_item['grade']}"
            )
        else:
            old_item = old_dict[key]
            if old_item['grade'] != new_item['grade']:
                # Оценка изменилась
                old_icon = "✅" if old_item['passed'] else "❌"
                if "неудовл" in old_item['grade'].lower(): old_icon = "❌"
                new_icon = "✅" if new_item['passed'] else "❌"
                if "неудовл" in new_item['grade'].lower(): new_icon = "❌"
                
                notifications.append(
                    f"🔄 *Изменение оценки!*\n"
                    f"🎓 {new_item.get('course', 'Неизвестный курс')} | 📅 {new_item.get('semester', 'Семестр')}\n"
                    f"*{new_item['subject']}*\n"
                    f"Было: {old_icon} _{old_item['grade']}_\n"
                    f"Стало: {new_icon} *{new_item['grade']}*"
                )
                
    return notifications

async def run_session_tracking(bot: Bot):
    logging.info("⏳ Запуск фоновой проверки результатов сессии...")
    users = await get_users_with_record_books()
    
    if not users:
        logging.info("Нет пользователей с привязанными зачетками.")
        return
        
    for user_id, record_book_number in users:
        try:
            old_data, last_updated = await get_cached_session_results(record_book_number)
            status, new_data = await UsurtScraper.get_session_results(record_book_number, use_cache=False)
            
            if status == "SUCCESS" and new_data:
                # Compare and notify if we had previous data
                if old_data:
                    notifications = compare_session_results(old_data, new_data)
                    for notif in notifications:
                        try:
                            await bot.send_message(user_id, notif, parse_mode="Markdown")
                            logging.info(f"Отправлено уведомление об оценке пользователю {user_id}")
                        except Exception as e:
                            logging.error(f"Ошибка отправки уведомления {user_id}: {e}")
                            
                # Save new data cache happens automatically inside UsurtScraper when use_cache=False
                # but let's be explicit and ensure we're not constantly triggering updates on empty runs
                pass
                
        except Exception as e:
            logging.error(f"Ошибка при отслеживании сессии для {record_book_number}: {e}")
            
        await asyncio.sleep(2) # Задержка, чтобы не DDoS-ить сайт УрГУПС
    
    logging.info("✅ Фоновая проверка сессии завершена.")
