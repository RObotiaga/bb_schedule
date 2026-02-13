import logging
import re
from typing import List, Dict, Any
from playwright.async_api import async_playwright
from datetime import datetime, timedelta, timezone

from app.core.database import get_cached_session_results, save_cached_session_results

class UsurtScraper:
    BASE_URL = "http://report.usurt.ru/uspev.aspx"

    @staticmethod
    async def get_session_results(record_book_number: str, use_cache: bool = True) -> tuple[str, List[Dict[str, Any]] | None]:
        """
        Fetches session results.
        Returns: (status, data)
        Status: "SUCCESS", "NOT_FOUND", "ERROR"
        """
        if use_cache:
            cached_data, last_updated_str = await get_cached_session_results(record_book_number)
            if cached_data is not None:
                try:
                    last_updated = datetime.fromisoformat(last_updated_str)
                    if last_updated.tzinfo is None:
                        last_updated = last_updated.replace(tzinfo=timezone.utc)
                    
                    now = datetime.now(timezone.utc)
                    
                    if now - last_updated < timedelta(hours=1):
                        logging.info(f"Using cached session results for {record_book_number}")
                        return "SUCCESS", cached_data
                except Exception as e:
                    logging.warning(f"Cache date parse error: {e}")

        logging.info(f"Scraping session results for {record_book_number}...")
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(UsurtScraper.BASE_URL)
                
                input_selector = '[name="ReportViewer1$ctl00$ctl03$ctl00"]'
                await page.fill(input_selector, record_book_number)
                
                submit_selector = '[name="ReportViewer1$ctl00$ctl00"]'
                await page.click(submit_selector)
                
                await page.wait_for_load_state('networkidle')
                
                content = await page.content()
                if "Дисциплина" not in content:
                    if "не найден" in content or "Error" in content:
                         return "NOT_FOUND", None
                
                rows = page.locator("tr")
                count = await rows.count()

                results = []
                current_semester = "Неизвестный семестр"
                current_year = ""
                
                grade_keywords = [
                    "отлично", "хорошо", "удовлетворительно", "неудовлетворительно", 
                    "зачтено", "незачет", "недопуск", "не явился"
                ]
                
                for i in range(count):
                    row = rows.nth(i)
                    text_content = await row.inner_text()
                    if not text_content.strip(): continue
                    
                    cells = row.locator("td, th")
                    cell_count = await cells.count()
                    
                    cell_texts = []
                    for j in range(cell_count):
                        cell_texts.append((await cells.nth(j).inner_text()).strip())
                    
                    non_empty_cells = [c for c in cell_texts if c]
                    
                    # --- 1. Semester/Year Header Detection ---
                    if len(non_empty_cells) == 1:
                        text = non_empty_cells[0]
                        
                        if re.match(r'^\d{4}/\d{4}$', text):
                            current_year = text
                            continue
                            
                        if "семестр" in text.lower() or text.isdigit():
                            sem_label = f"{text} семестр" if text.isdigit() else text
                            
                            if current_year:
                                current_semester = f"{sem_label} ({current_year})"
                            else:
                                current_semester = sem_label
                            continue

                    # --- 2. Data Row Parsing ---
                    grade_index = -1
                    grade_text = ""
                    
                    for idx, cell in enumerate(cell_texts):
                        cell_lower = cell.lower()
                        if any(k in cell_lower for k in grade_keywords):
                            grade_index = idx
                            grade_text = cell
                            break
                    
                    if grade_index == -1:
                        continue 
                        
                    # --- 3. Extract Subject and Grade ---
                    kw_pattern = "|".join(grade_keywords)
                    regex = re.compile(r"(.+)\s+\((" + kw_pattern + r")\)\s*$", re.IGNORECASE)
                    
                    match = regex.match(grade_text)
                    if match:
                        subject = match.group(1).strip()
                        grade = match.group(2).strip()
                    else:
                        subject = " ".join([c for c in cell_texts[:grade_index] if c])
                        grade = grade_text
                    
                    if "Дисциплина" in subject: continue
                    if not subject.strip(): continue

                    # --- 4. Extract Date ---
                    date_val = ""
                    if grade_index < len(cell_texts) - 1:
                        date_val = cell_texts[grade_index + 1]

                    # --- 5. Parse Grade Value ---
                    grade_value = None
                    is_exam = False
                    passed = True
                    
                    grade_lower = grade.lower()
                    
                    if "отлично" in grade_lower:
                        grade_value = 5
                        is_exam = True
                    elif "хорошо" in grade_lower:
                        grade_value = 4
                        is_exam = True
                    elif "удовлетворительно" in grade_lower:
                        grade_value = 3
                        is_exam = True
                    elif "неудовлетворительно" in grade_lower:
                        grade_value = 2
                        is_exam = True
                        passed = False
                    elif "незачет" in grade_lower or "недопуск" in grade_lower or "не явился" in grade_lower:
                        passed = False
                    
                    results.append({
                        'semester': current_semester,
                        'subject': subject,
                        'grade': grade,
                        'date': date_val,
                        'grade_value': grade_value,
                        'is_exam': is_exam,
                        'passed': passed
                    })
                
                if results:
                    await save_cached_session_results(record_book_number, results)

                return "SUCCESS", results

            except Exception as e:
                logging.error(f"Error scraping USURT: {e}")
                return "ERROR", None
            finally:
                await browser.close()
