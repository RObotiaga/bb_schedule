import pytest
import sys
import os

# Ensure path is correct
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def test_imports():
    """
    Smoke test to verify that all modules can be imported without error.
    This catches syntax errors, missing dependencies, and circular imports.
    """
    try:
        from app.core import config, logger, database, state
        from app.services import schedule_api, schedule_sync
        from app.bot import main, keyboards, states, filters
        from app.bot.handlers import common, schedule, teachers, session, admin
        from app.web import app
        
        assert True
    except ImportError as e:
        pytest.fail(f"Import failed: {e}")
    except Exception as e:
        pytest.fail(f"An error occurred during import: {e}")
