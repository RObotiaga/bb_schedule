from app.core.database import load_structure_from_db

class GlobalState:
    STRUCTURED_DATA = {}
    FACULTIES_LIST = []
    ALL_TEACHERS_LIST = []

    @classmethod
    async def reload(cls):
        data, faculties, teachers = await load_structure_from_db()
        cls.STRUCTURED_DATA = data
        cls.FACULTIES_LIST = faculties
        cls.ALL_TEACHERS_LIST = teachers
