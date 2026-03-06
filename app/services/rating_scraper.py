"""
HTTP-парсер зачётных книжек УрГУПС для массового сбора рейтинговых данных.
Использует aiohttp вместо Playwright (~0.4с vs ~15с на запрос).
"""
import asyncio
import logging
import random
import re
from typing import List, Dict, Any, Optional

import aiohttp
from bs4 import BeautifulSoup

BASE_URL = "http://report.usurt.ru/uspev.aspx"

# Ротация User-Agent для снижения заметности
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
]

GRADE_KEYWORDS = [
    "отлично", "хорошо", "удовлетворительно", "неудовлетворительно",
    "зачтено", "незачет", "недопуск", "не явился",
]


def _extract_asp_fields(html: str) -> dict:
    """Извлекает скрытые ASP.NET поля (VIEWSTATE и др.) из HTML."""
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    asp_names = [
        "__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
        "__EVENTTARGET", "__EVENTARGUMENT",
    ]
    for name in asp_names:
        tag = soup.find("input", {"name": name})
        if tag:
            fields[name] = tag.get("value", "")

    # Скрытые поля ReportViewer
    for tag in soup.find_all("input", {"type": "hidden"}):
        field_name = tag.get("name", "")
        if field_name.startswith("ReportViewer1"):
            fields[field_name] = tag.get("value", "")

    return fields


def _parse_grade(grade_text: str) -> dict:
    """Парсит текст оценки в структурированные поля."""
    grade_lower = grade_text.lower()
    grade_value = None
    is_exam = False
    passed = True

    if "отлично" in grade_lower:
        grade_value, is_exam = 5, True
    elif "хорошо" in grade_lower:
        grade_value, is_exam = 4, True
    elif "удовлетворительно" in grade_lower and "не" not in grade_lower.split("удовлетворительно")[0][-3:]:
        grade_value, is_exam = 3, True
    elif "неудовлетворительно" in grade_lower:
        grade_value, is_exam, passed = 2, True, False
    elif "незачет" in grade_lower or "недопуск" in grade_lower or "не явился" in grade_lower:
        passed = False

    return {"grade_value": grade_value, "is_exam": is_exam, "passed": passed}


def _parse_html_results(html: str) -> List[Dict[str, Any]]:
    """Парсит HTML ответа в список предметов с оценками. Совместим с форматом UsurtScraper."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr")

    results = []
    current_year = ""
    current_course = ""
    current_semester_num = ""
    just_seen_year = False
    just_seen_course = False

    for row in rows:
        cells = row.find_all(["td", "th"])
        cell_texts = [c.get_text(strip=True) for c in cells]
        non_empty = [c for c in cell_texts if c]

        if not non_empty:
            continue

        # --- Заголовки семестра/года ---
        if len(non_empty) == 1:
            text = non_empty[0]

            if re.match(r'^\d{4}/\d{4}$', text):
                current_year = text
                just_seen_year = True
                just_seen_course = False
                continue

            if text.isdigit() or "семестр" in text.lower() or "курс" in text.lower():
                if just_seen_year:
                    current_course = text.replace(" курс", "").strip() if not text.isdigit() else text
                    just_seen_year = False
                    just_seen_course = True
                    continue
                elif just_seen_course:
                    current_semester_num = text.replace(" семестр", "").strip() if not text.isdigit() else text
                    just_seen_course = False
                    continue
                else:
                    current_semester_num = text.replace(" семестр", "").strip() if not text.isdigit() else text
                    continue

        # --- Строки с оценками ---
        grade_index = -1
        grade_text = ""
        for idx, cell in enumerate(cell_texts):
            if any(kw in cell.lower() for kw in GRADE_KEYWORDS):
                grade_index = idx
                grade_text = cell
                break

        if grade_index == -1:
            continue

        # Парсинг предмета: может быть "Предмет (Оценка)" или в отдельных ячейках
        kw_pattern = "|".join(GRADE_KEYWORDS)
        regex = re.compile(r"(.+)\s+\((" + kw_pattern + r")\)\s*$", re.IGNORECASE)
        match = regex.match(grade_text)

        if match:
            subject = match.group(1).strip()
            grade = match.group(2).strip()
        else:
            subject = " ".join(c for c in cell_texts[:grade_index] if c)
            grade = grade_text

        if "Дисциплина" in subject or not subject.strip():
            continue

        # Дата — ячейка после оценки
        date_val = cell_texts[grade_index + 1] if grade_index < len(cell_texts) - 1 else ""

        parsed = _parse_grade(grade)
        sem_str = (current_semester_num + " семестр") if current_semester_num.isdigit() else current_semester_num
        semester_label = f"{sem_str} ({current_year})" if current_year else sem_str

        results.append({
            "course": current_course,
            "semester": semester_label,
            "subject": subject,
            "grade": grade,
            "date": date_val,
            "grade_value": parsed["grade_value"],
            "is_exam": parsed["is_exam"],
            "passed": parsed["passed"],
        })

    return results


async def scrape_record_book(
    session: aiohttp.ClientSession,
    record_book_number: str,
) -> tuple[str, Optional[List[Dict[str, Any]]]]:
    """
    Парсит одну зачётку через HTTP.
    Returns: (status, data) — совместимо с UsurtScraper.get_session_results().
    """
    try:
        # Шаг 1: GET для ASP.NET токенов
        async with session.get(BASE_URL) as resp:
            if resp.status != 200:
                return "ERROR", None
            html = await resp.text()

        asp_fields = _extract_asp_fields(html)
        if not asp_fields.get("__VIEWSTATE"):
            logging.warning(f"Не найден __VIEWSTATE для {record_book_number}")
            return "ERROR", None

        # Шаг 2: POST с номером зачётки
        post_data = {**asp_fields}
        post_data["ReportViewer1$ctl00$ctl03$ctl00"] = record_book_number
        post_data["ReportViewer1$ctl00$ctl00"] = "Просмотр"

        async with session.post(BASE_URL, data=post_data) as resp:
            if resp.status != 200:
                return "ERROR", None
            html = await resp.text()

        # Шаг 3: Проверка и парсинг
        if "не найден" in html.lower() or ("Дисциплина" not in html and "Error" in html):
            return "NOT_FOUND", None

        results = _parse_html_results(html)
        if not results:
            return "NOT_FOUND", None

        return "SUCCESS", results

    except asyncio.TimeoutError:
        logging.warning(f"Таймаут при парсинге {record_book_number}")
        return "ERROR", None
    except Exception as e:
        logging.error(f"Ошибка HTTP-парсинга {record_book_number}: {e}")
        return "ERROR", None


async def scrape_all_records(
    year: int = 2022,
    start: int = 1,
    max_consecutive_not_found: int = 20,
    delay_range: tuple = (2, 8),
    on_result=None,
    on_progress=None,
) -> dict:
    """
    Массовый парсинг зачёток за указанный год.
    
    Args:
        year: Год зачисления (префикс номера).
        start: Начальный порядковый номер.
        max_consecutive_not_found: Количество идущих подряд несуществующих зачеток для остановки парсинга года.
        delay_range: Мин/макс задержка между запросами (сек) для антифрода.
        on_result: Async callback(record_book, status, data) — вызывается после каждой зачётки.
        on_progress: Async callback(current, total) — вызывается каждые 50 записей (total может быть None).
    
    Returns: Статистика {total, success, not_found, error}.
    """
    from app.core.config import RATING_PARSER_WORKERS
    stats = {"total": 0, "success": 0, "not_found": 0, "error": 0}
    timeout = aiohttp.ClientTimeout(total=20) # Чуть больше для параллельности
    connector = aiohttp.TCPConnector(limit=RATING_PARSER_WORKERS + 1, force_close=True)
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    
    consecutive_not_found = 0
    current_num = start
    stop_event = asyncio.Event()
    semaphore = asyncio.Semaphore(RATING_PARSER_WORKERS)
    
    async def worker():
        nonlocal current_num, consecutive_not_found
        
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            while not stop_event.is_set():
                async with semaphore:
                    if stop_event.is_set():
                        break
                        
                    num = current_num
                    current_num += 1
                    record_book = f"{year}{num:04d}"
                    
                    # Ротация User-Agent
                    session.headers["User-Agent"] = random.choice(USER_AGENTS)
                
                status, data = await scrape_record_book(session, record_book)
                
                # Синхронное обновление статистики
                stats["total"] += 1
                stats[status.lower()] += 1
                
                if status == "SUCCESS":
                    consecutive_not_found = 0
                elif status == "NOT_FOUND":
                    # Считаем пропуски только «с конца» (грубо, но для остановки годится)
                    # Если мы нашли кого-то после пропуска, счетчик сбросится выше
                    consecutive_not_found += 1
                
                if on_result:
                    await on_result(record_book, status, data)
                
                actual_processed = stats["total"]
                absolute_progress = (start - 1) + actual_processed
                
                if on_progress:
                    await on_progress(absolute_progress, None)

                if actual_processed % 10 == 0:
                    logging.info(
                        f"Прогресс парсинга ({year}): {absolute_progress} "
                        f"(✅ {stats['success']}, ❌ {stats['error']}, 🔍 {stats['not_found']})"
                    )

                if consecutive_not_found >= max_consecutive_not_found:
                    logging.info(f"Достигнут предел пропусков для {year} года. Завершаем.")
                    stop_event.set()
                    break

                # Небольшая задержка перед следующим запросом в этом воркере
                await asyncio.sleep(random.uniform(*delay_range))

    # Запускаем группу воркеров
    workers = [worker() for _ in range(RATING_PARSER_WORKERS)]
    await asyncio.gather(*workers)

    logging.info(f"Парсинг {year} года завершён: {stats}")
    return stats
