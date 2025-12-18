
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_hierarchical_parsing():
    """
    Simulates the structure:
    2022/2023
      1
        Subject A - Passed
    2023/2024
      1
        Subject B - Passed
        
    Expects: "1 семестр (2022/2023)" and "1 семестр (2023/2024)"
    """
    # Mock row data
    # Format: text_content
    # effectively simulating rows.nth(i).inner_text() and cells parsing
    
    # Since the logic is tightly coupled with Playwright calls (await rows.count(), etc),
    # it is hard to unit test without refactoring.
    # Refactoring `UsurtScraper` to separate extraction from playwright is best practice.
    pass

# For now, I will create a small script that basically implements the NEW logic 
# and runs it against a list of simulating strings, to verify the ALGORITHM.

import re

def parse_simulated_rows(rows_data):
    results = []
    current_semester = "Неизвестный семестр"
    current_year = ""
    
    grade_keywords = ["зачтено", "отлично"]
    
    for row_text in rows_data:
        text = row_text.strip()
        
        # Simulate "non_empty_cells" logic
        # For this test, assume single values are headers, "Subj Grade" are data
        
        # 1. Header Detection
        if "/" in text and len(text) == 9: # Year heuristic for test
             if re.match(r'^\d{4}/\d{4}$', text):
                current_year = text
                continue
        
        if text.isdigit() or "семестр" in text:
             sem_label = f"{text} семестр" if text.isdigit() else text
             if current_year:
                 current_semester = f"{sem_label} ({current_year})"
             else:
                 current_semester = sem_label
             continue
             
        # 2. Data
        # assume "Subject Grade"
        if " " in text:
             results.append({"subject": text.split()[0], "semester": current_semester})
             
    return results

def test_algorithm():
    data = [
        "2022/2023",
        "1",
        "Math 5",
        "2023/2024",
        "1",
        "History 5"
    ]
    
    results = parse_simulated_rows(data)
    
    print("Results:")
    for r in results:
        print(r)
        
    assert results[0]['semester'] == "1 семестр (2022/2023)"
    assert results[1]['semester'] == "1 семестр (2023/2024)"
    print("Algorithm Verification Passed!")

if __name__ == "__main__":
    test_algorithm()
