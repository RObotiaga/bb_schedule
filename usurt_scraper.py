import logging
import re
from typing import List, Dict, Any
from playwright.async_api import async_playwright

class UsurtScraper:
    BASE_URL = "http://report.usurt.ru/uspev.aspx"

    @staticmethod
    async def get_session_results(record_book_number: str, use_cache: bool = True) -> List[Dict[str, Any]] | None:
        """
        Fetches session results.
        If use_cache is True, tries to load from DB first (TTL 1 hour).
        """
        from database import get_cached_session_results, save_cached_session_results
        from datetime import datetime, timedelta, timezone

        if use_cache:
            cached_data, last_updated_str = await get_cached_session_results(record_book_number)
            if cached_data and last_updated_str:
                # Check TTL (1 hour)
                # SQLite CURRENT_TIMESTAMP is usually UTC
                try:
                    last_updated = datetime.fromisoformat(last_updated_str)
                    # Ensure timezone awareness if needed, but usually simple diff works if both are same source
                    # Assuming UTC for simplicity or naive
                    if last_updated.tzinfo is None:
                        last_updated = last_updated.replace(tzinfo=timezone.utc)
                    
                    now = datetime.now(timezone.utc)
                    
                    if now - last_updated < timedelta(hours=1):
                        logging.info(f"Using cached session results for {record_book_number}")
                        return cached_data
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
                         return None # Indicate error/not found
                
                rows = page.locator("tr")
                count = await rows.count()

                results = []
                current_semester = "Неизвестный семестр"
                
                # Keywords to identify a grade cell
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
                    
                    # Filter empty strings from cell_texts for logic checks
                    non_empty_cells = [c for c in cell_texts if c]
                    
                    # --- 1. Semester Header Detection ---
                    # Check if row is a semester header.
                    # Criteria: 
                    # - Single non-empty cell
                    # - Contains "семестр" OR is just a digit (1, 2, 3...)
                    if len(non_empty_cells) == 1:
                        text = non_empty_cells[0]
                        if "семестр" in text.lower() or text.isdigit():
                            current_semester = f"{text} семестр" if text.isdigit() else text
                            continue

                    # --- 2. Data Row Parsing ---
                    # Find the cell that contains the grade
                    grade_index = -1
                    grade_text = ""
                    
                    for idx, cell in enumerate(cell_texts):
                        cell_lower = cell.lower()
                        if any(k in cell_lower for k in grade_keywords):
                            grade_index = idx
                            grade_text = cell
                            # We found a grade, stop looking (assuming grade is the first match or we take the first one)
                            # Usually grade is unique in the row.
                            break
                    
                    if grade_index == -1:
                        continue # Not a result row
                        
                    # --- 3. Extract Subject and Grade ---
                    # Check for "Subject (Grade)" format
                    # Regex to find (Grade) at the end
                    kw_pattern = "|".join(grade_keywords)
                    # Regex: Capture Subject (group 1) and Grade (group 2) inside parens at the end
                    regex = re.compile(r"(.+)\s+\((" + kw_pattern + r")\)\s*$", re.IGNORECASE)
                    
                    match = regex.match(grade_text)
                    if match:
                        # Combined cell
                        subject = match.group(1).strip()
                        grade = match.group(2).strip() # Extract just the grade text
                    else:
                        # Separate cells
                        # Subject is all text before the grade cell
                        subject = " ".join([c for c in cell_texts[:grade_index] if c])
                        grade = grade_text
                    
                    # Cleanup subject if needed
                    if "Дисциплина" in subject: continue
                    if not subject.strip(): continue

                    # --- 4. Extract Date ---
                    # Usually the cell after the grade
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
                        'grade': grade, # Now contains clean grade or full text if not split
                        'date': date_val,
                        'grade_value': grade_value,
                        'is_exam': is_exam,
                        'passed': passed
                    })
                
                # Save to cache
                if results:
                    await save_cached_session_results(record_book_number, results)

                return results

            except Exception as e:
                logging.error(f"Error scraping USURT: {e}")
                return None
            finally:
                await browser.close()
