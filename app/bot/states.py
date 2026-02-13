from aiogram.fsm.state import State, StatesGroup

class TeacherSearch(StatesGroup):
    name = State()
    matches = State()

class Broadcast(StatesGroup):
    waiting_for_message = State()

class SessionResults(StatesGroup):
    waiting_for_record_book_number = State()

class NoteEdit(StatesGroup):
    waiting_for_note_text = State()

class ChecklistAdd(StatesGroup):
    waiting_for_item_text = State()
