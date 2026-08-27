import asyncio
import gzip
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.bot.filters import IsAdmin
from app.bot.keyboards import admin_keyboard
from app.bot.states import Broadcast, DatabaseBackup, RatingImport
from app.core.database import initialize_database, restore_database_bytes
from app.core.repositories.user import get_all_user_ids
from app.core.state import GlobalState
from app.services.db_transfer import import_rating_data

logger = logging.getLogger(__name__)
router = Router()

_CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True,
)


def _confirm_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"{prefix}:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}:cancel"),
            ]
        ]
    )


async def _download_document_bytes(bot, file_id: str) -> bytes:
    file_info = await bot.get_file(file_id)
    file_obj = await bot.download_file(file_info.file_path)
    return file_obj.read()


# --- Broadcast: preview + explicit confirmation ---

@router.message(IsAdmin(), F.text == "📢 Рассылка")
async def broadcast_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Broadcast.waiting_for_message)
    await message.answer(
        "📝 Отправьте сообщение или медиа для рассылки.\n\n"
        "Сначала будет показан предпросмотр, и только после подтверждения сообщение уйдёт пользователям.",
        reply_markup=_CANCEL_KEYBOARD,
    )


@router.message(IsAdmin(), Broadcast.waiting_for_message, F.text == "❌ Отмена")
async def broadcast_cancel_waiting(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Рассылка отменена.", reply_markup=admin_keyboard)


@router.message(IsAdmin(), Broadcast.waiting_for_message)
async def broadcast_preview(message: Message, state: FSMContext):
    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
    )
    await state.set_state(Broadcast.confirming_message)

    await message.answer("👁 Предпросмотр рассылки:")
    await message.copy_to(chat_id=message.chat.id)
    users = await get_all_user_ids()
    await message.answer(
        f"Отправить это сообщение {len(users)} пользователям?",
        reply_markup=_confirm_keyboard("ux_broadcast"),
    )


@router.callback_query(IsAdmin(), Broadcast.confirming_message, F.data == "ux_broadcast:cancel")
async def broadcast_cancel_confirm(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.")
    await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
    await callback.answer()


@router.callback_query(IsAdmin(), Broadcast.confirming_message, F.data == "ux_broadcast:confirm")
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    source_chat_id = data.get("broadcast_chat_id")
    source_message_id = data.get("broadcast_message_id")
    users = await get_all_user_ids()
    await state.clear()

    if not source_chat_id or not source_message_id:
        await callback.answer("Предпросмотр устарел. Начните рассылку заново.", show_alert=True)
        return

    await callback.message.edit_text(f"🚀 Рассылка: 0/{len(users)}")
    await callback.answer()

    success = 0
    failed = 0
    for index, user_id in enumerate(users, start=1):
        try:
            await callback.bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            success += 1
        except TelegramAPIError:
            failed += 1
        except Exception:
            logger.exception("Broadcast failed for user %s", user_id)
            failed += 1

        if index % 25 == 0 or index == len(users):
            try:
                await callback.message.edit_text(
                    f"🚀 Рассылка: {index}/{len(users)}\n"
                    f"✅ Успешно: {success} · ❌ Ошибок: {failed}"
                )
            except TelegramAPIError:
                pass
        await asyncio.sleep(0.05)

    await callback.message.edit_text(
        f"✅ Рассылка завершена.\n\nУспешно: {success}\nНе удалось: {failed}"
    )
    await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)


@router.message(IsAdmin(), Broadcast.confirming_message)
async def broadcast_confirming_guard(message: Message):
    await message.answer("Используйте кнопки «Подтвердить» или «Отмена» под предпросмотром.")


# --- Rating import: state-scoped document handler + confirmation ---

@router.message(IsAdmin(), F.text == "📥 Импорт рейтинга")
async def rating_import_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(RatingImport.waiting_for_file)
    await message.answer(
        "📥 Отправьте экспорт рейтинга в формате .json или .json.gz.",
        reply_markup=_CANCEL_KEYBOARD,
    )


@router.message(IsAdmin(), RatingImport.waiting_for_file, F.text == "❌ Отмена")
async def rating_import_cancel_waiting(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Импорт рейтинга отменён.", reply_markup=admin_keyboard)


@router.message(IsAdmin(), RatingImport.waiting_for_file, F.document)
async def rating_import_receive(message: Message, state: FSMContext):
    filename = message.document.file_name or ""
    if not (filename.endswith(".json") or filename.endswith(".json.gz") or filename.endswith(".gz")):
        await message.answer("❌ Нужен файл .json или .json.gz.")
        return

    await state.update_data(
        rating_file_id=message.document.file_id,
        rating_filename=filename,
        rating_file_size=message.document.file_size or 0,
    )
    await state.set_state(RatingImport.confirming_file)
    size_mb = (message.document.file_size or 0) / (1024 * 1024)
    await message.answer(
        f"Импортировать рейтинг из «{filename}» ({size_mb:.2f} МБ)?\n"
        "Текущие рейтинговые данные будут обновлены.",
        reply_markup=_confirm_keyboard("ux_rating_import"),
    )


@router.message(IsAdmin(), RatingImport.waiting_for_file)
async def rating_import_waiting_guard(message: Message):
    await message.answer("Отправьте файл .json/.json.gz или нажмите «❌ Отмена».")


@router.callback_query(IsAdmin(), RatingImport.confirming_file, F.data == "ux_rating_import:cancel")
async def rating_import_cancel_confirm(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Импорт рейтинга отменён.")
    await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
    await callback.answer()


@router.callback_query(IsAdmin(), RatingImport.confirming_file, F.data == "ux_rating_import:confirm")
async def rating_import_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_id = data.get("rating_file_id")
    filename = data.get("rating_filename", "")
    if not file_id:
        await state.clear()
        await callback.answer("Файл не найден. Начните импорт заново.", show_alert=True)
        return

    await callback.message.edit_text("📥 Импортирую рейтинг…")
    await callback.answer()
    try:
        raw = await _download_document_bytes(callback.bot, file_id)
        if filename.endswith(".gz"):
            raw = gzip.decompress(raw)
        success = await import_rating_data(raw.decode("utf-8"))
        if not success:
            raise ValueError("Формат рейтинга не распознан")
        await state.clear()
        await callback.message.edit_text("✅ Рейтинг успешно импортирован.")
        await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
    except Exception as exc:
        logger.exception("Rating import failed")
        await state.clear()
        await callback.message.edit_text(f"❌ Импорт не выполнен: {exc}")
        await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)


@router.message(IsAdmin(), RatingImport.confirming_file)
async def rating_import_confirming_guard(message: Message):
    await message.answer("Используйте кнопки подтверждения под выбранным файлом.")


# --- Database restore: same safe Online Backup path as the Web admin ---

@router.message(IsAdmin(), F.text == "📥 Загрузить БД")
async def db_import_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(DatabaseBackup.waiting_for_db_file)
    await message.answer(
        "📥 Отправьте резервную копию bb_schedule в формате .db.\n\n"
        "Файл будет сначала выбран, а восстановление начнётся только после отдельного подтверждения.",
        reply_markup=_CANCEL_KEYBOARD,
    )


@router.message(IsAdmin(), DatabaseBackup.waiting_for_db_file, F.text == "❌ Отмена")
async def db_import_cancel_waiting(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Восстановление БД отменено.", reply_markup=admin_keyboard)


@router.message(IsAdmin(), DatabaseBackup.waiting_for_db_file, F.document)
async def db_import_receive(message: Message, state: FSMContext):
    filename = message.document.file_name or ""
    if not filename.lower().endswith(".db"):
        await message.answer("❌ Нужен файл с расширением .db.")
        return

    await state.update_data(
        db_file_id=message.document.file_id,
        db_filename=filename,
        db_file_size=message.document.file_size or 0,
    )
    await state.set_state(DatabaseBackup.confirming_db)
    size_mb = (message.document.file_size or 0) / (1024 * 1024)
    await message.answer(
        f"⚠️ Восстановить рабочую базу из «{filename}» ({size_mb:.2f} МБ)?\n\n"
        "Файл будет проверен на целостность и схему bb_schedule. "
        "После подтверждения данные рабочей базы будут заменены содержимым копии.",
        reply_markup=_confirm_keyboard("ux_db_import"),
    )


@router.message(IsAdmin(), DatabaseBackup.waiting_for_db_file)
async def db_import_waiting_guard(message: Message):
    await message.answer("Отправьте файл .db или нажмите «❌ Отмена».")


@router.callback_query(IsAdmin(), DatabaseBackup.confirming_db, F.data == "ux_db_import:cancel")
async def db_import_cancel_confirm(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Восстановление БД отменено.")
    await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
    await callback.answer()


@router.callback_query(IsAdmin(), DatabaseBackup.confirming_db, F.data == "ux_db_import:confirm")
async def db_import_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_id = data.get("db_file_id")
    if not file_id:
        await state.clear()
        await callback.answer("Файл не найден. Начните восстановление заново.", show_alert=True)
        return

    await callback.message.edit_text("📥 Проверяю и восстанавливаю базу…")
    await callback.answer()
    try:
        raw = await _download_document_bytes(callback.bot, file_id)
        await restore_database_bytes(raw)
        await initialize_database()
        await GlobalState.reload()
        await state.clear()
        await callback.message.edit_text("✅ База успешно восстановлена и структура перезагружена.")
        await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
    except Exception as exc:
        logger.exception("Database restore failed")
        await state.clear()
        await callback.message.edit_text(f"❌ Восстановление не выполнено: {exc}")
        await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)


@router.message(IsAdmin(), DatabaseBackup.confirming_db)
async def db_import_confirming_guard(message: Message):
    await message.answer("Используйте кнопки подтверждения под выбранной резервной копией.")


# A document outside an explicit import flow must never be interpreted silently.
@router.message(IsAdmin(), F.document)
async def admin_document_fallback(message: Message):
    await message.answer(
        "Файл не импортирован. Сначала выберите «📥 Импорт рейтинга» или «📥 Загрузить БД» в админ-панели.",
        reply_markup=admin_keyboard,
    )
