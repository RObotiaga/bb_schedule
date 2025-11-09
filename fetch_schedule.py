# FILE: fetch_schedule.py
import asyncio
import os
import re 
import sys
import logging
import shutil
from urllib.parse import urljoin, unquote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from decouple import config

# --- КОНФИГУРАЦИЯ ---
LOGIN = config("BB_LOGIN", default=None)
PASSWORD = config("BB_PASSWORD", default=None)

if not LOGIN or not PASSWORD:
    print("Критическая ошибка: Не найдены переменные окружения BB_LOGIN/BB_PASSWORD.")
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
    print(f"Переходим в папку '{week_name}'...")
    
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
    print("Ищем ссылки на папки факультетов...")
    
    # --- ИСПРАВЛЕНИЕ 2: Используем точный селектор для содержимого таблицы ---
    # Ищем ссылки (папки), которые ведут на frameset, только внутри тела списка элементов.
    folder_selector = 'tbody#listContainer_databody a[href*="action=frameset"]'

    try:
        await page.locator(folder_selector).first.wait_for(timeout=10000)
    except PlaywrightTimeout:
        logging.warning("Не найдено ни одной папки факультетов на странице.")
        return []

    folder_locators = await page.locator(folder_selector).all()
    # Собираем только уникальные ссылки
    folder_links = [await loc.get_attribute('href') for loc in folder_locators if await loc.get_attribute('href')]
    
    return list(dict.fromkeys(folder_links))

async def download_xls_files(page, faculty_name: str):
    """Скачивает файлы, раскладывая их по папкам 'Факультет/Курс'."""
    # Ищем файлы, используя более общий селектор (вдруг они тоже не в li.title)
    file_selector = 'a[href$=".xls"], a[href$=".xlsx"]'
    try:
        # Ждем файлы в течение 10 секунд
        await page.locator(file_selector).first.wait_for(timeout=10000)
    except PlaywrightTimeout:
        logging.info("  Файлы для скачивания не найдены.")
        return 0

    links = await page.locator(file_selector).all()
    logging.info(f"  Найдено файлов для скачивания: {len(links)}")
    count = 0
    for el in links:
        try:
            async with page.expect_download() as download_info:
                await el.click(timeout=15000)
            
            download = await download_info.value
            filename = download.suggested_filename

            course_match = re.search(r'(\d)\s*курс', filename, re.IGNORECASE)
            if course_match:
                course_folder = f"{course_match.group(1)} курс"
            else:
                course_folder = "Без курса"

            target_dir = os.path.join(DOWNLOAD_DIR, faculty_name, course_folder)
            os.makedirs(target_dir, exist_ok=True)
            
            save_path = os.path.join(target_dir, filename)
            await download.save_as(save_path)
            
            logging.info(f"    - Скачан в: {os.path.relpath(save_path)}")
            count += 1
        except PlaywrightTimeout as e:
            print(f"    - Не удалось скачать файл (таймаут). Ошибка: {e}")
            continue
        except Exception as e:
            print(f"    - Не удалось скачать файл. Неизвестная ошибка: {type(e).__name__}: {e}")
            continue
    return count

async def process_faculty_folders(page):
    faculty_list_url = page.url
    folder_links = await get_faculty_folder_links(page)
    print(f'Найдено папок факультетов: {len(folder_links)}')
    
    if not folder_links:
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

        print(f'\nОбрабатываем папку {i}/{len(folder_links)}: "{decoded_folder_name}"...')
        
        count = await download_xls_files(page, decoded_folder_name)
        logging.info(f'  Скачано файлов из папки: {count}')
        
        if i < len(folder_links):
            print("  Возвращаемся к списку факультетов...")
            # Принудительный возврат к списку факультетов
            await page.goto(faculty_list_url)
            await page.wait_for_load_state("networkidle")

async def main():
    ensure_download_dir()
    browser_args = ['--no-sandbox', '--disable-setuid-sandbox']
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=browser_args)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page() 
        try:
            print("--- START LOGIN ---")
            await login(page)
            print("--- LOGIN SUCCESS ---")

            page = await click_schedule_root(page, context)
            
            week_selection_page_url = page.url
            week_types_to_process = ["Нечетная неделя", "Четная неделя"]
            
            for week_name in week_types_to_process:
                print(f"\n{'='*20} НАЧИНАЕМ ОБРАБОТКУ: {week_name.upper()} {'='*20}")
                
                await page.goto(week_selection_page_url)
                await page.wait_for_load_state("networkidle")
                
                await navigate_to_week_folder(page, week_name)
                
                # После перехода в папку Недели, запускаем обработку факультетов
                await process_faculty_folders(page)
                
            logging.info(f"\n{'='*20} ВСЕ НЕДЕЛИ УСПЕШНО ОБРАБОТАНЫ {'='*20}")

        except Exception as e:
            print(f"\nПроизошла фатальная ошибка: {type(e).__name__}: {e}")
            sys.exit(1) 
        finally:
            logging.info("Закрываем браузер...")
            await browser.close()

if __name__ == "__main__":
    if os.path.exists(os.path.join(os.getcwd(), '..')):
        sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), '..')))
        
    # Дополнительная проверка на наличие .env
    if not config("BB_LOGIN", default=None):
        print("Критическая ошибка: Не найдены переменные окружения BB_LOGIN/BB_PASSWORD. Проверьте ваш .env файл.")
        sys.exit(1)
        
    asyncio.run(main())