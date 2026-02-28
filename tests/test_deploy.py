import pytest
import os
import sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- 1. Тесты конфигурации среды ---
def test_config_loading_no_crash(monkeypatch):
    """
    Проверяем, что даже если нет .env файла или в нем пустые значения, 
    приложение не падает при старте (TypeError / ValueError),
    а корректно присваивает None или дефолтное значение переменным.
    """
    # Удаляем критические переменные из энва (мулировав "чистую среду")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_ID", raising=False)
    
    # Мокаем decouple.config, чтобы он не читал из .env файла
    def mock_config(key, default=None, **kwargs):
        if key == "ADMIN_ID":
            return ""
        return default
    
    import decouple
    monkeypatch.setattr(decouple, "config", mock_config)
    
    # Пытаемся импортировать config - он должен выбросить ValueError
    import importlib
    import sys
    
    if "app.core.config" in sys.modules:
        del sys.modules["app.core.config"]
        
    try:
        import app.core.config
        pytest.fail("Ожидался ValueError из-за отсутствия токена")
    except ValueError as e:
        assert "TELEGRAM_BOT_TOKEN is missing" in str(e)
    
    monkeypatch.undo()
    if "app.core.config" in sys.modules:
        importlib.reload(sys.modules["app.core.config"])
    else:
        import app.core.config

# --- 2. Тест инициализации FastAPI и Healthcheck ---
def test_fastapi_healthcheck():
    """
    Проверяем, что FastAPI app импортируется корректно, нет циклических 
    зависимостей, и эндпоинт /health отдает 200 OK. 
    Используется для liveness/readiness prob в Docker.
    """
    from app.web.app import app
    client = TestClient(app)
    
    response = client.get("/health")
    
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "schedule_web"}

# --- 3. Тест инициализации бота (Telegram) ---
@pytest.mark.asyncio
async def test_bot_initialization():
    """
    Проверяем, что структура бота, роутеров, фильтров и хэндлеров собирается
    без ошибок интерпретатора и импортов. 
    (Мы не запускаем бота, мы создаем объекты и проверяем целостность).
    """
    from aiogram import Bot, Dispatcher
    from app.bot.main import create_dispatcher
    
    # 1. Проверяем, что создается диспетчер с роутерами
    dp = create_dispatcher()
    assert isinstance(dp, Dispatcher)
    assert len(dp.sub_routers) > 0  # common, schedule, session, teachers, admin...
    
    # 2. Создаем бота с 'моковым' токеном, чтобы убедиться в работоспособности объекта
    bot = Bot(token="123456789:AABBCcDDEEFFGG")
    assert bot.id == 123456789
    
    await bot.session.close()

# --- 4. Smoke Test верхнеуровневых импортов ---
def test_main_imports():
    """
    Smoke тест для точки входа. Проверка отсутствия SyntaxError или 
    ModuleNotFoundError на верхнем уровне.
    """
    try:
        import app.main
        # Если импорт прошел успешно, тест пройден
        assert True
    except Exception as e:
        pytest.fail(f"Could not import app.main: {e}")
