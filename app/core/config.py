import os
import sys
from decouple import config

# Определяем базовую директорию проекта (на уровень выше от app/core/config.py -> app/core -> app -> root)
# Или просто os.getcwd() если запускаем из корня
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None)
_admin_id_str = config("ADMIN_ID", default="")
ADMIN_ID = int(_admin_id_str) if _admin_id_str.strip().isdigit() else None

BB_LOGIN = config("BB_LOGIN", default=None)
BB_PASSWORD = config("BB_PASSWORD", default=None)
BB_URL = config("BB_URL", default="https://bb.usurt.ru/")

# Пути к данным
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "schedule.db")
DOWNLOAD_DIR = os.path.join(DATA_DIR, "schedules")

# Настройки парсинга рейтинга
_parsing_years_str = config("PARSING_YEARS", default="")
if _parsing_years_str:
    PARSING_YEARS = [int(y.strip()) for y in _parsing_years_str.split(",") if y.strip().isdigit()]
else:
    # Автоматический расчет: последние 6 лет
    from datetime import datetime
    now = datetime.now()
    if now.month < 7:
        # Первая половина года: от (тек_год - 6) до (тек_год - 1)
        PARSING_YEARS = list(range(now.year - 6, now.year))
    else:
        # Вторая половина года: от (тек_год - 5) до тек_год
        PARSING_YEARS = list(range(now.year - 5, now.year + 1))

MAX_CONSECUTIVE_NOT_FOUND = config("MAX_CONSECUTIVE_NOT_FOUND", default=20, cast=int)
RATING_PARSER_WORKERS = config("RATING_PARSER_WORKERS", default=3, cast=int)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "schedule.db")
DOWNLOAD_DIR = os.path.join(DATA_DIR, "schedules")

# Создаем директории если их нет
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Проверка критических переменных
if not TELEGRAM_BOT_TOKEN:
    print("Критическая ошибка: TELEGRAM_BOT_TOKEN не задан!")
    raise ValueError("TELEGRAM_BOT_TOKEN is missing")

if not ADMIN_ID:
    print("Критическая ошибка: ADMIN_ID не задан!")
    sys.exit(1)
