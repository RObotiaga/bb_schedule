# Используем образ с установленными зависимостями Playwright
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

# Устанавливаем рабочую директорию
WORKDIR /app

# Сначала копируем только requirements.txt для кэширования слоя с зависимостями
COPY requirements.txt .

# Устанавливаем Python-зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальной код проекта
COPY . .

# Убеждаемся, что директория для данных существует
RUN mkdir -p /app/data/schedules

# Запуск бота
CMD ["python", "bot.py"]