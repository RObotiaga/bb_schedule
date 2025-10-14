import asyncio
import os
import re # <-- Добавлен импорт
from urllib.parse import urljoin, unquote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

LOGIN = os.getenv("BB_LOGIN", "AVSofronov")
PASSWORD = os.getenv("BB_PASSWORD", "rW2qsFW4LirpH_J")
BB_URL = "https://bb.usurt.ru/"
DOWNLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "schedules"))

def ensure_download_dir():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

async def login(page):
    await page.goto(BB_URL)
    try:
        await page.locator('button#agree_button').click(timeout=3000)
    except PlaywrightTimeout:
        pass
    
    await page.locator('input[type="text"]').fill(LOGIN)
    await page.locator('input[type="password"]').fill(PASSWORD)
    await page.locator('button, input[type="submit"]').click()
    await page.wait_for_load_state('networkidle')

async def click_schedule_root(page, context):
    print("Ожидаем открытия новой вкладки после клика...")
    async with context.expect_page() as new_page_info:
        await page.locator('a[href*="xid-1859775_1"]').click()

    new_page = await new_page_info.value
    print(f"Новая вкладка открыта: {await new_page.title()}")
    await new_page.wait_for_load_state("networkidle")
    print("Новая вкладка полностью загружена.")
    return new_page

async def navigate_to_week_folder(page, week_name):
    print(f"Переходим в папку '{week_name}'...")
    week_link = page.locator("li.title").get_by_role(
        "link", 
        name=week_name, 
        exact=True
    )
    
    await asyncio.gather(
        page.wait_for_load_state('networkidle'),
        week_link.click()
    )
    print("Переход выполнен.")

async def get_faculty_folder_links(page):
    print("Ищем ссылки на папки факультетов...")
    folder_selector = 'li.title > a[href*="action=frameset"]'

    try:
        await page.locator(folder_selector).first.wait_for(timeout=10000)
    except PlaywrightTimeout:
        print("Не найдено ни одной папки факультетов на странице.")
        return []

    folder_locators = await page.locator(folder_selector).all()
    folder_links = [await loc.get_attribute('href') for loc in folder_locators if await loc.get_attribute('href')]
    return list(dict.fromkeys(folder_links))

async def download_xls_files(page, faculty_name: str):
    """Скачивает файлы, раскладывая их по папкам 'Факультет/Курс'."""
    file_selector = 'li.title a[href$=".xls"], li.title a[href$=".xlsx"]'
    try:
        await page.locator(file_selector).first.wait_for(timeout=5000)
    except PlaywrightTimeout:
        print("  Файлы для скачивания не найдены.")
        return 0

    links = await page.locator(file_selector).all()
    print(f"  Найдено файлов для скачивания: {len(links)}")
    count = 0
    for el in links:
        try:
            async with page.expect_download() as download_info:
                await el.click(timeout=10000)
            
            download = await download_info.value
            filename = download.suggested_filename

            # Извлекаем номер курса из имени файла
            course_match = re.search(r'(\d)\s*курс', filename, re.IGNORECASE)
            if course_match:
                course_folder = f"{course_match.group(1)} курс"
            else:
                course_folder = "Без курса"

            # Создаем целевую директорию
            target_dir = os.path.join(DOWNLOAD_DIR, faculty_name, course_folder)
            os.makedirs(target_dir, exist_ok=True)
            
            save_path = os.path.join(target_dir, filename)
            await download.save_as(save_path)
            
            print(f"    - Скачан в: {os.path.relpath(save_path)}")
            count += 1
        except PlaywrightTimeout as e:
            print(f"    - Не удалось скачать файл (таймаут). Ошибка: {e}")
            continue
    return count

async def process_faculty_folders(page):
    """
    Находит все папки факультетов, заходит в каждую, скачивает файлы
    и возвращается назад.
    """
    faculty_list_url = page.url
    folder_links = await get_faculty_folder_links(page)
    print(f'Найдено папок факультетов: {len(folder_links)}')
    
    for i, folder_href in enumerate(folder_links, 1):
        decoded_folder_name = unquote(folder_href.split('/')[-1].split('?')[0])
        print(f'\nОбрабатываем папку {i}/{len(folder_links)}: "{decoded_folder_name}"...')
        
        full_url = urljoin(BB_URL, folder_href)
        await page.goto(full_url)
        await page.wait_for_load_state("networkidle")
        
        count = await download_xls_files(page, decoded_folder_name)
        print(f'  Скачано файлов из папки: {count}')
        
        if i < len(folder_links):
            print("  Возвращаемся к списку факультетов...")
            await page.goto(faculty_list_url)
            await page.wait_for_load_state("networkidle")

async def main():
    ensure_download_dir()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page() 
        try:
            await login(page)
            page = await click_schedule_root(page, context)
            
            week_selection_page_url = page.url
            week_types_to_process = ["Нечетная неделя", "Четная неделя"]
            
            for week_name in week_types_to_process:
                print(f"\n{'='*20} НАЧИНАЕМ ОБРАБОТКУ: {week_name.upper()} {'='*20}")
                await page.goto(week_selection_page_url)
                await page.wait_for_load_state("networkidle")
                await navigate_to_week_folder(page, week_name)
                await process_faculty_folders(page)
                
            print(f"\n{'='*20} ВСЕ НЕДЕЛИ УСПЕШНО ОБРАБОТАНЫ {'='*20}")

        except Exception as e:
            print(f"\nПроизошла фатальная ошибка: {e}")
            await page.screenshot(path="fatal_error_screenshot.png")
            with open("fatal_error_page.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            print("Скриншот и HTML страницы сохранены для анализа ошибки.")
        finally:
            print("Закрываем браузер...")
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())