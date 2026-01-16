# FILE: fetch_schedule.py
import asyncio
import os
import re 
import sys
import logging
import shutil
from datetime import datetime
from urllib.parse import urljoin, unquote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from decouple import config
from utils import setup_logging

# --- КОНФИГУРАЦИЯ ---
LOGIN = config("BB_LOGIN", default=None)
PASSWORD = config("BB_PASSWORD", default=None)

if not LOGIN or not PASSWORD:
    logging.error("Критическая ошибка: Не найдены переменные окружения BB_LOGIN/BB_PASSWORD.")
    sys.exit(1)

BB_URL = "https://bb.usurt.ru/"
from config import DOWNLOAD_DIR

def ensure_download_dir():
    # Удаляем старую директорию, если она есть
    if os.path.exists(DOWNLOAD_DIR):
        logging.info(f"Удаляем старую папку с расписаниями: {DOWNLOAD_DIR}")
        shutil.rmtree(DOWNLOAD_DIR)
    # Создаем пустую директорию
    logging.info(f"Создаем пустую папку для расписаний: {DOWNLOAD_DIR}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ... (Остальные функции login, click_schedule_root, navigate_to_week_folder, 
# get_faculty_folder_links, download_xls_files, process_faculty_folders заменены на logging) ...

async def login(page):
    await page.goto(BB_URL)
    try:
        await page.wait_for_selector('button#agree_button', timeout=5000)
        await page.locator('button#agree_button').click()
    except PlaywrightTimeout:
        pass
    
    await page.locator('input[type="text"]').fill(LOGIN)
    await page.locator('input[type="password"]').fill(PASSWORD)
    await page.locator('button, input[type="submit"]').click()
    await page.wait_for_load_state('networkidle')

async def click_schedule_root(page, context):
    logging.info("Ожидаем открытия новой вкладки после клика...")
    async with context.expect_page() as new_page_info:
        await page.locator('a[href*="xid-1859775_1"]').click()

    new_page = await new_page_info.value
    logging.info(f"Новая вкладка открыта: {await new_page.title()}")
    await new_page.wait_for_load_state("networkidle")
    logging.info("Новая вкладка полностью загружена.")
    return new_page

async def navigate_to_week_folder(page, week_name):
    logging.info(f"Переходим в папку '{week_name}'...")
    
    # ИСПРАВЛЕНИЕ 1: Ищем ссылку по роли, это сработало
    week_link = page.get_by_role(
        "link", 
        name=week_name, 
        exact=True
    )
    
    await asyncio.gather(
        page.wait_for_load_state('networkidle'),
        week_link.click()
    )
    logging.info("Переход выполнен.")

async def get_faculty_folder_links(page):
    logging.info("Ищем ссылки на папки факультетов...")
    
    # --- ИСПРАВЛЕНИЕ 2: Используем точный селектор для содержимого таблицы ---
    # Ищем ссылки (папки), которые ведут на frameset, только внутри тела списка элементов.
    folder_selector = 'tbody#listContainer_databody a[href*="action=frameset"]'

    try:
        await page.locator(folder_selector).first.wait_for(timeout=10000)
    except PlaywrightTimeout:
        logging.warning("Не найдено ни одной папки факультетов на странице.")
        return []

    # Оптимизация: получаем все атрибуты href за один вызов JS, чтобы избежать таймаутов при итерации
    folder_links = await page.locator(folder_selector).evaluate_all("els => els.map(e => e.getAttribute('href'))")
    # Фильтруем None и пустые строки
    folder_links = [href for href in folder_links if href]
    
    return list(dict.fromkeys(folder_links))

async def download_xls_files(page, faculty_name: str, week_name_prefix: str = ""):
    """
    Скачивает все xls/xlsx файлы со страницы.
    """
    # Обычно мы уже внутри папки курса. Но если файлы прямо в факультете?
    # Или если мы в "Общем"?
    # Попробуем определить курс из хлебных крошек или текущего URL, или просто свалим в кучу.
    
    current_url = page.url
    # Если "Общее" - значит мы в корне сессии/недели.
    if faculty_name == "Общее":
        save_dir = os.path.join(DOWNLOAD_DIR, faculty_name)
    else:
        # Стандартная структура папок факультета
        save_dir = os.path.join(DOWNLOAD_DIR, faculty_name)
        if not os.path.exists(save_dir):
            os.makedirs(save_dir, exist_ok=True)
            
    if not os.path.exists(save_dir):
        os.makedirs(save_dir, exist_ok=True)
    
    # ВОЗВРАЩАЕМСЯ К СУТИ:
    # Если мы нашли файлы в КОРНЕ (где нет факультетов), мы их качаем.
    # Сохраним их в папку: data/schedules/DirectDownloads/{filename}
    # Чтобы process_schedules их нашел.
    
    save_dir = os.path.join(DOWNLOAD_DIR, faculty_name)
    if not os.path.exists(save_dir):
        os.makedirs(save_dir, exist_ok=True)

    # Ищем файлы
    file_selector = 'a[href$=".xls"], a[href$=".xlsx"]'
    try:
        # Ждем файлы в течение 2 секунд (быстрый скип пустых папок)
        await page.locator(file_selector).first.wait_for(timeout=2000)
    except PlaywrightTimeout:
        logging.info("  Файлы для скачивания не найдены.")
        return 0

    # Получаем элементы
    files = await page.locator(file_selector).all()
    count = 0
    
    for file_link in files:
        url = await file_link.get_attribute('href')
        if not url: continue
            
        full_url = url if url.startswith('http') else urljoin(page.url, url)
        
        # Скачивание
        try:
            async with page.expect_download() as download_info:
                # Используем JS клик для надежности
                await file_link.evaluate("node => node.click()")
            
            download = await download_info.value
            
            # Имя файла
            original_filename = download.suggested_filename
            
            # ! ДОБАВЛЕНИЕ ПРЕФИКСА !
            if week_name_prefix:
                # Очистка префикса от недопустимых символов
                safe_prefix = re.sub(r'[\\/*?:"<>|]', '_', week_name_prefix).strip()
                final_filename = f"{safe_prefix}_{original_filename}"
            else:
                final_filename = original_filename
                
            save_path = os.path.join(save_dir, final_filename)
            
            await download.save_as(save_path)
            logging.info(f"    - Скачан в: {save_path}")
            count += 1
            
        except Exception as e:
            logging.error(f"Ошибка при скачивании {url}: {e}")
            
    return count

async def process_faculty_folders(page, week_name: str):
    faculty_list_url = page.url
    folder_links = await get_faculty_folder_links(page)
    logging.info(f'Найдено папок факультетов: {len(folder_links)}')
    
    if not folder_links:
        logging.warning("Не найдено ни одной папки факультетов на странице.")
        
        # ЭВРИСТИКА: Возможно, файлы лежат прямо здесь (без папок факультетов)?
        # Попробуем найти файлы прямо в текущей папке.
        logging.info("Попытка найти файлы прямо в текущей папке (без подпапок)...")

        # Передаем "Общее" как имя факультета.
        files_found = await download_xls_files(page, "Общее", week_name_prefix=week_name)
        if files_found > 0:
            logging.info(f"Найдено и скачано {files_found} файлов в корневой папке недели.")
            return

        logging.info("Файлы и папки не найдены. Пропуск.")
        return

    for i, folder_href in enumerate(folder_links, 1):
        # Имя папки берем из текста ссылки, т.к. это надежнее, чем декодировать URL
        
        # Переходим по ссылке и ожидаем загрузки
        full_url = urljoin(BB_URL, folder_href)
        await page.goto(full_url)
        await page.wait_for_load_state("networkidle")
        
        # Получаем имя факультета (текст ссылки) после навигации, 
        # чтобы корректно назвать папку
        
        # Поскольку мы находимся внутри папки факультета, 
        # мы можем попытаться получить его имя из URL или заголовка, 
        # но надежнее всего получить его из последнего сегмента URL.
        try:
             # Парсим путь из URL
             decoded_path = unquote(page.url.split('?')[0])
             decoded_folder_name = os.path.basename(decoded_path)
        except Exception:
            # Fallback
            decoded_folder_name = f"Факультет_{i}"

        logging.info(f'\nОбрабатываем папку {i}/{len(folder_links)}: "{decoded_folder_name}"...')
        
        count = await download_xls_files(page, decoded_folder_name, week_name)
        logging.info(f'  Скачано файлов из папки: {count}')
        
        if i < len(folder_links):
            logging.info("  Возвращаемся к списку факультетов...")
            # Принудительный возврат к списку факультетов
            await page.goto(faculty_list_url)
            await page.wait_for_load_state("networkidle")

async def is_session_relevant(week_name: str) -> bool:
    """
    Проверяет, является ли сессия актуальной для текущей даты.
    Логика:
    - Сентябрь (9) - Январь (1): Актуальна сессия за 1 семестр.
    - Февраль (2) - Июль (7): Актуальна сессия за 2 семестр.
    """
    # Если это не сессия, то она актуальна (обычные недели)
    if 'аттестация' not in week_name.lower() and 'сессия' not in week_name.lower():
        return True

    now = datetime.now()
    month = now.month
    year = now.year

    # Определяем ожидаемый семестр
    # 1 семестр: примерно с сентября по январь/февраль
    if 9 <= month <= 12 or month == 1:
        target_semester = 1
        # Учебный год:
        # Если сейчас сент-дек 2025 -> 2025-2026
        # Если сейчас янв 2026 -> 2025-2026
        if month == 1:
            start_year = year - 1
        else:
            start_year = year
    # 2 семестр: февраль - июль/август
    else:
        target_semester = 2
        start_year = year - 1 if month < 9 else year # (обычно весна это тот же учебный год, что начался в прошлом)
        # Если сейчас фев-июль 2026 -> учебный год начался в 2025 -> 2025-2026

    # Парсим имя (ожидаем формат "...1 семестр 2025-2026...")
    match = re.search(r'(\d)\s*семестр.*?(\d{4})[/-](\d{4})', week_name, re.IGNORECASE)
    if match:
        sem = int(match.group(1))
        y_start = int(match.group(2))
        
        # Строгая проверка: семестр должен совпадать, и год начала тоже.
        if sem == target_semester and y_start == start_year:
            return True
            
        logging.info(f"Пропуск неактуальной сессии: {week_name} (Ожидалось: {target_semester} сем. {start_year}-{start_year+1})")
        return False
    
    # Если не удалось распарсить год/семестр, но это сессия - по умолчанию пропускаем, чтобы не качать мусор,
    # ИЛИ (безопаснее) качаем, если не уверены.
    # Решим: качаем только если уверены, что актуально.
    return False

async def get_week_folder_links(page):
    """Динамически ищет папки недель на странице."""
    logging.info("Ищем ссылки на папки недель...")
    folder_selector = 'tbody#listContainer_databody a[href*="action=frameset"]'
    
    try:
        await page.locator(folder_selector).first.wait_for(timeout=10000)
    except PlaywrightTimeout:
        logging.warning("Не найдено ни одной папки недель на странице.")
        return {}

    # Оптимизация: получаем данные (href и текст) за один вызов JS
    items = await page.locator(folder_selector).evaluate_all("""
        els => els.map(e => ({
            href: e.getAttribute('href'),
            text: e.innerText
        }))
    """)
    
    week_links = {}
    for item in items:
        href = item['href']
        name = item['text']
        if href and name:
            name_lower = name.lower()
            if ('неделя' in name_lower or 
                'четная' in name_lower or 
                'нечет' in name_lower or 
                'промежуточная аттестация' in name_lower or
                'сессия' in name_lower):
                
                # Фильтрация сессий
                is_relevant = await is_session_relevant(name)
                if is_relevant:
                    week_links[name] = href
                    logging.info(f"  [+] Добавлена ссылка: {name}")
                else:
                    if 'аттестация' in name_lower or 'сессия' in name_lower:
                        logging.info(f"  [-] Пропущена сессия: {name}")
            else:
                 # Логируем, что мы видим, но игнорируем (для отладки)
                 # logging.debug(f"  [.] Игнорируем ссылку: {name}")
                 pass
            
    return week_links

async def main():
    ensure_download_dir()
    browser_args = ['--no-sandbox', '--disable-setuid-sandbox']
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=browser_args)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page() 
        try:
            logging.info("--- START LOGIN ---")
            await login(page)
            logging.info("--- LOGIN SUCCESS ---")

            page = await click_schedule_root(page, context)
            
            week_selection_page_url = page.url
            
            # Динамически получаем папки недель
            week_folders = await get_week_folder_links(page)
            
            if not week_folders:
                logging.error("Не удалось найти папки для четной/нечетной недели. Прерывание.")
                sys.exit(1)

            for week_name, week_href in week_folders.items():
                logging.info(f"\n{'='*20} НАЧИНАЕМ ОБРАБОТКУ: {week_name.upper()} {'='*20}")
                
                # Возвращаемся на страницу выбора недель
                await page.goto(week_selection_page_url)
                await page.wait_for_load_state("networkidle")
                
                # Переходим в папку недели
                await navigate_to_week_folder(page, week_name)
                
                # После перехода в папку Недели, запускаем обработку факультетов
                await process_faculty_folders(page, week_name)
                
            logging.info(f"\n{'='*20} ВСЕ НЕДЕЛИ УСПЕШНО ОБРАБОТАНЫ {'='*20}")

        except Exception as e:
            logging.error(f"\nПроизошла фатальная ошибка: {type(e).__name__}: {e}", exc_info=True)
            sys.exit(1) 
        finally:
            logging.info("Закрываем браузер...")
            await browser.close()

if __name__ == "__main__":
    setup_logging()
    if os.path.exists(os.path.join(os.getcwd(), '..')):
        sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), '..')))
        
    asyncio.run(main())