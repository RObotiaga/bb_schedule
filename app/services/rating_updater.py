"""
Фоновая задача: массовый парсинг зачёток и обновление рейтинга.
Запускается раз в сутки через scheduler.
"""
import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime

from app.core.database import save_rating_record, save_job_log, cleanup_old_job_logs
from app.services.rating_scraper import scrape_all_records
from app.services.clustering import run_clustering
from app.services.cluster_mapper import map_clusters_to_groups
from app.services.subject_stats import calculate_subject_stats


def _compute_stats(data: list) -> dict:
    """Считает статистику из списка предметов."""
    total = len(data)
    passed = sum(1 for item in data if item.get("passed", False))
    pass_rate = (passed / total * 100) if total > 0 else 0.0

    # Последний учебный год — ищем максимальный
    years = set()
    for item in data:
        semester = item.get("semester", "")
        match = re.search(r'(\d{4}/\d{4})', semester)
        if match:
            years.add(match.group(1))

    last_year = max(years) if years else ""
    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(pass_rate, 2),
        "last_academic_year": last_year,
    }


async def _on_record_parsed(record_book: str, status: str, data: list | None):
    """Callback: сохраняет результат парсинга одной зачётки в БД."""
    if status != "SUCCESS" or not data:
        return

    # Год зачисления — первые 4 цифры номера зачётки
    enrollment_year = int(record_book[:4])
    stats = _compute_stats(data)

    await save_rating_record(
        record_book=record_book,
        enrollment_year=enrollment_year,
        subjects_json=json.dumps(data, ensure_ascii=False),
        total_subjects=stats["total"],
        passed_subjects=stats["passed"],
        pass_rate=stats["pass_rate"],
        last_academic_year=stats["last_academic_year"],
    )


async def run_rating_update(bot=None, status_message=None):
    """
    Полный цикл обновления рейтинга:
    1. Парсинг всех зачёток за указанные года
    2. Кластеризация
    3. Маппинг кластеров на группы расписания
    4. Расчёт статистики преподавателей
    """
    from app.core.database import save_rating_record, save_job_log, cleanup_old_job_logs, get_last_parsed_num, get_records_count_by_year
    from app.core.config import ADMIN_ID, PARSING_YEARS, MAX_CONSECUTIVE_NOT_FOUND
    start_time = datetime.now()
    logging.info(f"🏆 Начало обновления рейтинга: {start_time}...")
    
    details = {}
    status = "ERROR"

    try:
        total_years = len(PARSING_YEARS)
        
        # Предварительная оценка общего объема работ
        estimated_total_all = 0
        for y in PARSING_YEARS:
            estimated_total_all += await get_records_count_by_year(y)
        
        start_timestamp = time.time()
        total_processed_in_session = 0
        
        def make_progress_callback(current_year: int, year_idx: int, estimated_total_year: int):
            last_update_time = 0
            
            async def _on_progress(current_absolute_in_year: int, total_in_session_override: int | None):
                nonlocal total_processed_in_session, last_update_time
                
                # Троттлинг обновлений сообщения (раз в 5 секунд)
                now = time.time()
                if now - last_update_time < 5:
                    return

                if bot and status_message:
                    # Базовый процент для уже пройденных лет
                    base_percent = (year_idx / total_years) * 100
                    
                    # Добавляем прогресс внутри текущего года
                    year_weight = 100 / total_years
                    inner_progress = 0
                    if estimated_total_year > 0:
                        inner_progress = min(current_absolute_in_year / (estimated_total_year or 1), 0.99)
                    
                    overall_percent = base_percent + (inner_progress * year_weight)
                    
                    # Расчет ETA
                    elapsed = now - start_timestamp
                    total_processed_in_session += 1 # Грубая прибавка при каждом вызове
                    
                    eta_str = "считаю..."
                    if elapsed > 10 and total_processed_in_session > 5:
                        speed = total_processed_in_session / elapsed 
                        percent_left = max(0, 100 - overall_percent)
                        if overall_percent > 0:
                            remaining_sec = (elapsed / overall_percent) * percent_left
                            if remaining_sec > 60:
                                eta_str = f"~{int(remaining_sec / 60)} мин"
                            else:
                                eta_str = f"~{int(remaining_sec)} сек"

                    # Индикатор активности
                    dot = "•" if int(now) % 2 == 0 else "◦"
                    
                    try:
                        await bot.edit_message_text(
                            f"🏆 Обновление рейтинга (парсинг зачёток + кластеризация)...\n"
                            f"📊 Общий прогресс: {overall_percent:.1f}%\n"
                            f"📁 Год: {current_year} (найдено {current_absolute_in_year}) {dot}\n"
                            f"⏳ Осталось: {eta_str}",
                            chat_id=status_message.chat.id,
                            message_id=status_message.message_id
                        )
                        last_update_time = now
                    except Exception as e:
                        if "message is not modified" not in str(e):
                            if "flood control exceeded" in str(e).lower():
                                # Если всё равно поймали флуд, увеличиваем задержку
                                last_update_time = now + 30 
                            logging.error(f"Failed to edit progress message: {e}")
            return _on_progress

        aggregated_stats = {"total": 0, "success": 0, "not_found": 0, "error": 0}

        # Шаг 1 & 2: Массовый парсинг и кластеризация для каждого года
        for i, year in enumerate(PARSING_YEARS):
            logging.info(f"Начинаем проверку года {year}...")
            
            # Получаем оценку общего количества для прогресс-бара
            estimated_total_year = await get_records_count_by_year(year)
            
            # Проверяем, можно ли продолжить парсинг
            last_parsed = await get_last_parsed_num(year)
            start_num = last_parsed + 1
            if start_num > 1:
                logging.info(f"♻️ Возобновляем парсинг {year} года с номера {start_num:04d} (последний был {last_parsed:04d} за последние 24ч)")

            stats = await scrape_all_records(
                year=year,
                start=start_num,
                max_consecutive_not_found=MAX_CONSECUTIVE_NOT_FOUND,
                delay_range=(2, 8),
                on_result=_on_record_parsed,
                on_progress=make_progress_callback(year, i, estimated_total_year),
            )
            logging.info(f"📊 Парсинг {year} завершён: {stats}")
            
            for k, v in stats.items():
                if k in aggregated_stats:
                    aggregated_stats[k] += v

            # Кластеризация и определение отчисленных для года
            await run_clustering(enrollment_year=year)

        if bot and status_message:
            try:
                await bot.edit_message_text(
                    f"🏆 Обновление рейтинга...\n"
                    f"✅ Парсинг завершён.\n"
                    f"⚙️ Выполняется маппинг и расчёт статистики...",
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id
                )
            except Exception:
                pass

        details.update(aggregated_stats)

        # Шаг 3: Маппинг кластеров на группы расписания
        await map_clusters_to_groups()
        logging.info("🗺️ Маппинг кластеров завершён")

        # Шаг 4: Расчёт статистики предметов
        await calculate_subject_stats()
        logging.info("📊 Статистика предметов рассчитана")
        
        status = "SUCCESS"
        logging.info("✅ Обновление рейтинга завершено")
        
        if ADMIN_ID and bot:
            try:
                msg = (
                    "✅ *Автоматическое обновление рейтинга*\n\n"
                    f"Данные успешно спаршены ({aggregated_stats.get('total', 0)} запросов, "
                    f"успешно: {aggregated_stats.get('success', 0)}). "
                    "Кластеры и статистика преподавателей обновлены."
                )
                if status_message:
                    await bot.edit_message_text(
                        msg,
                        chat_id=status_message.chat.id,
                        message_id=status_message.message_id,
                        parse_mode="Markdown"
                    )
                else:
                    await bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление об успехе рейтинга: {e}")
        
    except Exception as e:
        status = "ERROR"
        details["error"] = str(e)
        logging.exception("Ошибка при обновлении рейтинга")
        
        if ADMIN_ID and bot:
            try:
                await bot.send_message(
                    ADMIN_ID, 
                    "❌ *Ошибка авто-обновления рейтинга*\n\nПроизошла ошибка при фоновом обновлении и расчете статистики. Проверьте логи.", 
                    parse_mode="Markdown"
                )
            except Exception as e_msg:
                logging.error(f"Не удалось отправить уведомление об ошибке рейтинга: {e_msg}")
        
    finally:
        end_time = datetime.now()
        duration = end_time - start_time
        details["duration_seconds"] = duration.total_seconds()
        
        try:
            await save_job_log("rating_update", start_time, end_time, status, details)
            await cleanup_old_job_logs(days=30)
        except Exception as e_log:
            logging.error(f"Не удалось сохранить лог задачи: {e_log}")
