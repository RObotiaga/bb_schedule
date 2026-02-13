import asyncio
import os
import re
import shutil
import logging
import sqlite3
import pandas as pd
from datetime import datetime
from urllib.parse import urljoin, unquote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from app.core.config import DB_PATH, DOWNLOAD_DIR, BB_LOGIN, BB_PASSWORD, BB_URL
from app.core.logger import setup_logging

# --- CONSTANTS ---
MONTHS_MAP = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
}

class ScheduleFetcher:
    def __init__(self):
        self.download_dir = DOWNLOAD_DIR
        self.login = BB_LOGIN
        self.password = BB_PASSWORD
        self.base_url = BB_URL

    def ensure_download_dir(self):
        if os.path.exists(self.download_dir):
            logging.info(f"Удаляем старую папку с расписаниями: {self.download_dir}")
            shutil.rmtree(self.download_dir)
        logging.info(f"Создаем пустую папку для расписаний: {self.download_dir}")
        os.makedirs(self.download_dir, exist_ok=True)

    async def login_to_bb(self, page):
        await page.goto(self.base_url)
        try:
            await page.wait_for_selector('button#agree_button', timeout=5000)
            await page.locator('button#agree_button').click()
        except PlaywrightTimeout:
            pass
        
        await page.locator('input[type="text"]').fill(self.login)
        await page.locator('input[type="password"]').fill(self.password)
        await page.locator('button, input[type="submit"]').click()
        await page.wait_for_load_state('networkidle')

    async def click_schedule_root(self, page, context):
        logging.info("Ожидаем открытия новой вкладки после клика...")
        async with context.expect_page() as new_page_info:
            await page.locator('a[href*="xid-1859775_1"]').click()

        new_page = await new_page_info.value
        logging.info(f"Новая вкладка открыта: {await new_page.title()}")
        await new_page.wait_for_load_state("networkidle")
        return new_page

    async def navigate_to_week_folder(self, page, week_name):
        logging.info(f"Переходим в папку '{week_name}'...")
        week_link = page.get_by_role("link", name=week_name, exact=True)
        await asyncio.gather(page.wait_for_load_state('networkidle'), week_link.click())

    async def get_faculty_folder_links(self, page):
        logging.info("Ищем ссылки на папки факультетов...")
        folder_selector = 'tbody#listContainer_databody a[href*="action=frameset"]'
        try:
            await page.locator(folder_selector).first.wait_for(timeout=10000)
        except PlaywrightTimeout:
            logging.warning("Не найдено ни одной папки факультетов на странице.")
            return []
        folder_links = await page.locator(folder_selector).evaluate_all("els => els.map(e => e.getAttribute('href'))")
        return list(dict.fromkeys([href for href in folder_links if href]))

    async def download_xls_files(self, page, faculty_name: str, week_name_prefix: str = ""):
        save_dir = os.path.join(self.download_dir, faculty_name)
        if not os.path.exists(save_dir):
            os.makedirs(save_dir, exist_ok=True)

        file_selector = 'a[href$=".xls"], a[href$=".xlsx"]'
        try:
            await page.locator(file_selector).first.wait_for(timeout=2000)
        except PlaywrightTimeout:
            return 0

        files = await page.locator(file_selector).all()
        count = 0
        for file_link in files:
            url = await file_link.get_attribute('href')
            if not url: continue
            
            try:
                async with page.expect_download() as download_info:
                    await file_link.evaluate("node => node.click()")
                download = await download_info.value
                original_filename = download.suggested_filename
                
                if week_name_prefix:
                    safe_prefix = re.sub(r'[\\/*?:"<>|]', '_', week_name_prefix).strip()
                    final_filename = f"{safe_prefix}_{original_filename}"
                else:
                    final_filename = original_filename
                    
                await download.save_as(os.path.join(save_dir, final_filename))
                count += 1
            except Exception as e:
                logging.error(f"Ошибка при скачивании {url}: {e}")
        return count

    async def process_faculty_folders(self, page, week_name: str):
        faculty_list_url = page.url
        folder_links = await self.get_faculty_folder_links(page)
        
        if not folder_links:
            files_found = await self.download_xls_files(page, "Общее", week_name_prefix=week_name)
            if files_found > 0:
                logging.info(f"Найдено и скачано {files_found} файлов в корневой папке недели.")
            return

        for i, folder_href in enumerate(folder_links, 1):
            full_url = urljoin(self.base_url, folder_href)
            await page.goto(full_url)
            await page.wait_for_load_state("networkidle")
            
            try:
                 decoded_path = unquote(page.url.split('?')[0])
                 decoded_folder_name = os.path.basename(decoded_path)
            except Exception:
                decoded_folder_name = f"Факультет_{i}"

            logging.info(f'Обрабатываем папку {i}/{len(folder_links)}: "{decoded_folder_name}"...')
            await self.download_xls_files(page, decoded_folder_name, week_name)
            
            if i < len(folder_links):
                await page.goto(faculty_list_url)
                await page.wait_for_load_state("networkidle")

    async def is_session_relevant(self, week_name: str) -> bool:
        if 'аттестация' not in week_name.lower() and 'сессия' not in week_name.lower():
            return True

        now = datetime.now()
        month = now.month
        year = now.year

        if 9 <= month <= 12 or month == 1:
            target_semester = 1
            start_year = year - 1 if month == 1 else year
        else:
            target_semester = 2
            start_year = year - 1 if month < 9 else year

        match = re.search(r'(\d)\s*семестр.*?(\d{4})[/-](\d{4})', week_name, re.IGNORECASE)
        if match:
            sem = int(match.group(1))
            y_start = int(match.group(2))
            if sem == target_semester and y_start == start_year:
                return True
            logging.info(f"Пропуск неактуальной сессии: {week_name}")
            return False
        return False

    async def get_week_folder_links(self, page):
        logging.info("Ищем ссылки на папки недель...")
        folder_selector = 'tbody#listContainer_databody a[href*="action=frameset"]'
        try:
            await page.locator(folder_selector).first.wait_for(timeout=10000)
        except PlaywrightTimeout:
            logging.warning("Не найдено ни одной папки недель на странице.")
            return {}

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
                if any(x in name_lower for x in ['неделя', 'четная', 'нечет', 'аттестация', 'сессия']):
                    if await self.is_session_relevant(name):
                        week_links[name] = href
        return week_links

    async def run(self):
        self.ensure_download_dir()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page() 
            try:
                logging.info("--- START LOGIN ---")
                await self.login_to_bb(page)
                logging.info("--- LOGIN SUCCESS ---")

                page = await self.click_schedule_root(page, context)
                week_selection_page_url = page.url
                week_folders = await self.get_week_folder_links(page)
                
                if not week_folders:
                    logging.error("Не удалось найти папки недель.")
                    return False

                for week_name, _ in week_folders.items(): # href not used directly, logical flow used goto
                    logging.info(f"=== ОБРАБОТКА: {week_name.upper()} ===")
                    await page.goto(week_selection_page_url)
                    await page.wait_for_load_state("networkidle")
                    await self.navigate_to_week_folder(page, week_name)
                    await self.process_faculty_folders(page, week_name)
                
                logging.info("=== ВСЕ НЕДЕЛИ ОБРАБОТАНЫ ===")
                return True

            except Exception as e:
                logging.error(f"Ошибка скрапинга: {e}", exc_info=True)
                return False
            finally:
                await browser.close()


class ScheduleProcessor:
    def __init__(self):
        self.db_path = DB_PATH
        self.schedules_dir = DOWNLOAD_DIR
        self.current_year = datetime.now().year

    def determine_week_type(self, filename):
        filename_lower = filename.lower()
        if 'нечетная' in filename_lower or 'нечет' in filename_lower: return 'нечетная'
        if 'четная' in filename_lower or 'чет' in filename_lower: return 'четная'
        if 'аттестация' in filename_lower or 'сессия' in filename_lower: return 'сессия'
        return 'неизвестно'

    def parse_filename_context(self, filename):
        match = re.search(r'(\d)\s*семестр.*?(\d{4})[/-](\d{4})', filename, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2)), int(match.group(3))
        return None

    def parse_date_from_cell(self, cell_content, context):
        if not isinstance(cell_content, str): return None
        
        date_match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})', cell_content)
        if date_match:
            try:
                day = int(date_match.group(1))
                month = int(date_match.group(2))
                year_str = date_match.group(3)
                year = int(year_str) + 2000 if len(year_str) == 2 else int(year_str)
                return datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                pass
                
        match = re.search(r'(\d+)\s+([а-я]+)', cell_content, re.IGNORECASE)
        if match:
            day = int(match.group(1))
            month = MONTHS_MAP.get(match.group(2).lower())
            if month:
                target_year = context.get('year', datetime.now().year)
                if 'semester' in context:
                    if context['semester'] == 1:
                        target_year = context['start_year'] if month >= 9 else context['end_year']
                    elif context['semester'] == 2:
                        target_year = context['end_year']
                else:
                    if month > 9 and datetime.now().month < 5:
                        target_year = datetime.now().year - 1
                    else:
                        target_year = datetime.now().year
                try:
                    return datetime(target_year, month, day).strftime('%Y-%m-%d')
                except ValueError:
                    return None
        return None

    def parse_lesson_cell(self, cell_content):
        if not isinstance(cell_content, str) or not cell_content.strip(): return None
        lines = [re.sub(r'^\s*-\s*', '', line).strip() for line in cell_content.split('\n') if line.strip()]
        if not lines: return None
            
        subject = lines[0]
        teacher = "Не указан"
        location_parts = []
        
        if len(lines) > 1:
            teacher_found = False
            for i in range(1, len(lines)):
                line = lines[i]
                if not teacher_found and line.lower() == "не указан": continue
                
                if not teacher_found:
                    is_academic = any(k in line.lower() for k in ["преподаватель", "доцент", "профессор", "ассистент", "зав. кафедрой"])
                    is_name_format = re.match(r'^[А-ЯЁ][а-яё\-]+\s+([А-ЯЁ]\.\s*[А-ЯЁ]\.|[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)', line)
                    if is_academic or is_name_format:
                        teacher = line
                        teacher_found = True
                    else:
                        location_parts.append(line)
                else:
                    location_parts.append(line)
            location = " ".join(location_parts) if location_parts else "Не указана"
        else:
            location = "Не указана"
            
        subgroup_match = re.search(r'(\d\s*п/г)', cell_content, re.IGNORECASE)
        if subgroup_match:
            subject += f" ({subgroup_match.group(1).replace(' ', '')})"
            
        return {"subject": subject, "teacher": teacher, "location": location}

    def process_single_file(self, file_path, faculty="Неизвестно", course="N/A"):
        lessons_list = []
        filename = os.path.basename(file_path)
        if not filename.endswith((".xls", ".xlsx")): return []
        
        week_type = self.determine_week_type(filename)
        if week_type == 'неизвестно': return []
        
        file_context = {}
        acad_context = self.parse_filename_context(filename)
        if acad_context:
            file_context.update({'semester': acad_context[0], 'start_year': acad_context[1], 'end_year': acad_context[2]})
        else:
            file_context['year'] = self.current_year
            
        try:
            df = pd.read_excel(file_path, header=None)
            header_row_index = -1
            for i, row in df.iterrows():
                row_str = [str(cell) for cell in row.tolist()]
                if 'День' in row_str or ('Day' in row_str and 'Time' in row_str):
                    header_row_index = i
                    break
            
            if header_row_index == -1: return []
            
            df = pd.read_excel(file_path, header=header_row_index)
            df.columns = [str(col).strip() for col in df.columns]
            
            day_col = 'День' if 'День' in df.columns else ('Day' if 'Day' in df.columns else None)
            time_col = 'Часы' if 'Часы' in df.columns else ('Time' if 'Time' in df.columns else None)
            
            if not day_col or not time_col: return []
            
            groups = [col for col in df.columns if col not in [day_col, time_col, 'nan', 'Unnamed: 0']]
            current_date_str = None
            current_time_slot = None
            
            for index, row in df.iterrows():
                potential_date = self.parse_date_from_cell(str(row.get(day_col)), file_context)
                if potential_date:
                    current_date_str = potential_date
                    current_time_slot = None
                
                if not current_date_str: continue
                
                raw_time = str(row.get(time_col, '')).strip()
                if raw_time and "nan" not in raw_time.lower():
                    time_slot = raw_time
                    current_time_slot = time_slot
                elif current_time_slot:
                    time_slot = current_time_slot
                else:
                    continue
                    
                for group in groups:
                    lesson_info = self.parse_lesson_cell(row.get(group))
                    if lesson_info:
                        lessons_list.append((
                            str(group).strip(), current_date_str, time_slot,
                            lesson_info['subject'], lesson_info['teacher'], lesson_info['location'],
                            week_type, faculty, course
                        ))
            return lessons_list
        except Exception as e:
            logging.error(f"Error processing {filename}: {e}")
            return []

    def run(self):
        if not os.path.exists(self.schedules_dir):
            logging.error(f"Directory {self.schedules_dir} not found.")
            return False

        conn = sqlite3.connect(self.db_path)
        all_lessons = []
        
        logging.info("Processing schedule files...")
        for dirpath, _, filenames in os.walk(self.schedules_dir):
            if dirpath == self.schedules_dir: continue
            
            relative_path = os.path.relpath(dirpath, self.schedules_dir)
            path_parts = relative_path.split(os.sep)
            faculty = path_parts[0] if path_parts else "Неизвестно"
            course_str = path_parts[1] if len(path_parts) > 1 else "Без курса"
            course = re.search(r'\d+', course_str).group(0) if re.search(r'\d+', course_str) else "N/A"
            
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                current_file_course = course
                if current_file_course == "N/A":
                    match = re.search(r'(\d+)\s*курс', filename, re.IGNORECASE)
                    if match: current_file_course = match.group(1)
                
                lessons = self.process_single_file(file_path, faculty, current_file_course)
                all_lessons.extend(lessons)

        if not all_lessons:
            logging.warning("No lessons found.")
            conn.close()
            return False

        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            cursor.execute("DELETE FROM schedule;")
            cursor.executemany("""
            INSERT INTO schedule (group_name, lesson_date, time, subject, teacher, location, week_type, faculty, course)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, all_lessons)
            conn.commit()
            logging.info(f"Updated DB with {len(all_lessons)} lessons.")
            return True
        except Exception as e:
            conn.rollback()
            logging.error(f"DB Update Error: {e}")
            return False
        finally:
            conn.close()

async def run_full_sync():
    fetcher = ScheduleFetcher()
    if await fetcher.run():
        processor = ScheduleProcessor()
        return processor.run()
    return False
