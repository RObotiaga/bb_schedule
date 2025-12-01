# Автотесты для проекта bb_schedule

Комплексное тестовое покрытие для Telegram-бота расписания USURT.

## Установка зависимостей

```bash
# Установка зависимостей для разработки
uv sync --dev

# Или через pip
pip install pytest pytest-asyncio pytest-mock pytest-cov faker
```

## Запуск тестов

### Все тесты
```bash
uv run pytest
```

### С отчетом о покрытии
```bash
uv run pytest --cov=. --cov-report=html
```
После выполнения откройте `htmlcov/index.html` в браузере.

### Конкретный модуль
```bash
uv run pytest tests/test_database.py -v
```

### С подробным выводом
```bash
uv run pytest -vv
```

### Только юнит-тесты
```bash
uv run pytest -m unit
```

## Структура тестов

```
tests/
├── conftest.py                  # Глобальные fixtures
├── test_database.py             # Тесты БД (CRUD, кэш, заметки)
├── test_usurt_scraper.py        # Тесты парсера результатов сессии
├── test_bot_handlers.py         # Тесты хэндлеров бота
└── test_process_schedules.py    # Тесты обработки Excel расписаний
```

## Покрытие

Тесты покрывают:
- ✅ **database.py**: Все CRUD операции, кэширование сессий, заметки
- ✅ **usurt_scraper.py**: Парсинг HTML, определение оценок, кэширование
- ✅ **bot.py**: Фильтры результатов, хэндлеры, FSM состояния
- ✅ **process_schedules.py**: Парсинг Excel, определение дат и типов недель

## Примеры

### Запуск одного теста
```bash
uv run pytest tests/test_database.py::test_save_and_get_user_group -v
```

### Отладка с pdb
```bash
uv run pytest --pdb
```

### Запуск с измерением времени
```bash
uv run pytest --durations=10
```

## CI/CD

Для интеграции в CI добавьте в `.github/workflows/test.yml`:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - uses: actions/setup-python@v2
      with:
        python-version: '3.10'
    - run: pip install uv
    - run: uv sync --dev
    - run: uv run pytest --cov=. --cov-report=xml
    - uses: codecov/codecov-action@v2
```

## Моки и фикстуры

### Доступные fixtures (из conftest.py):
- `test_db` - Временная БД для тестов
- `sample_session_results` - Тестовые данные результатов сессии
- `sample_html_table` - HTML таблица для парсера
- `mock_playwright_page` - Мок для Playwright
- `mock_bot` - Мок для aiogram Bot
- `mock_message` - Мок для Message
- `mock_callback_query` - Мок для CallbackQuery

### Использование fixtures:
```python
@pytest.mark.asyncio
async def test_example(test_db, sample_session_results):
    # test_db содержит путь к временной БД
    # sample_session_results содержит тестовые данные
    pass
```
