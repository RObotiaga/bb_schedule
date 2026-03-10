"""
Парсер зачётной книжки УрГУПС.
Использует HTTP requests (aiohttp + BeautifulSoup) вместо Playwright —
в ~37 раз быстрее: ~0.4с vs ~15с на одну зачётку.
"""
import logging
import random
from typing import List, Dict, Any
from datetime import datetime, timedelta, timezone

import aiohttp
from bs4 import BeautifulSoup

from app.core.repositories.subject import get_cached_session_results, save_cached_session_results
from app.services.rating_scraper import scrape_record_book

# Заголовки для имитации браузера
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
}


class UsurtScraper:
    BASE_URL = "http://report.usurt.ru/uspev.aspx"

    @staticmethod
    async def get_session_results(
        record_book_number: str, use_cache: bool = True
    ) -> tuple[str, List[Dict[str, Any]] | None]:
        """
        Получает результаты сессии по номеру зачётки.
        Returns: (status, data)
        Status: "SUCCESS", "NOT_FOUND", "ERROR"
        """
        # --- Кэш ---
        if use_cache:
            cached_data, last_updated_str = await get_cached_session_results(record_book_number)
            if cached_data is not None:
                try:
                    last_updated = datetime.fromisoformat(last_updated_str)
                    if last_updated.tzinfo is None:
                        last_updated = last_updated.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - last_updated < timedelta(hours=1):
                        logging.info(f"Using cached session results for {record_book_number}")
                        return "SUCCESS", cached_data
                except Exception as e:
                    logging.warning(f"Cache date parse error: {e}")

        # --- Запрос ---
        logging.info(f"HTTP-парсинг зачётки {record_book_number}...")
        timeout = aiohttp.ClientTimeout(total=15)
        connector = aiohttp.TCPConnector(force_close=True)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector, headers=_DEFAULT_HEADERS
            ) as session:
                status, results = await scrape_record_book(session, record_book_number)

            if status == "SUCCESS" and results:
                await save_cached_session_results(record_book_number, results)

            return status, results

        except Exception as e:
            logging.error(f"Ошибка получения данных для {record_book_number}: {e}")
            return "ERROR", None
