# Используем образ с установленными зависимостями Playwright
# СТАРОЕ: FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта
COPY requirements.txt .
COPY config.py .           
COPY database.py .         
COPY fetch_schedule.py .
COPY process_schedules.py .
COPY usurt_scraper.py .
COPY bot.py .

# Устанавливаем Python-зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Убеждаемся, что директория для данных существует
RUN mkdir -p /app/data/schedules

# Запуск бота
CMD ["python", "bot.py"]