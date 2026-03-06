from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from app.core.database import get_subjects_with_stats, get_subject_rating
from app.bot.keyboards import get_subjects_keyboard

router = Router()

@router.message(Command("top_subjects"))
async def cmd_top_subjects(message: Message, state: FSMContext):
    subjects = await get_subjects_with_stats()
    if not subjects:
        await message.answer("📭 Данных по предметам пока нет.")
        return
        
    await state.update_data(cached_subjects=subjects)
    keyboard = get_subjects_keyboard(subjects, page=0)
    await message.answer("📚 <b>Выберите предмет для просмотра рейтинга:</b>", reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("subj_page:"))
async def process_subj_page(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":")[1])
    data = await state.get_data()
    subjects = data.get("cached_subjects")
    
    if not subjects:
        subjects = await get_subjects_with_stats()
        await state.update_data(cached_subjects=subjects)
        
    if not subjects:
        await callback.answer("Ошибка: нет данных.")
        return
        
    keyboard = get_subjects_keyboard(subjects, page=page)
    # Check if text is same to avoid error
    current_text = "📚 <b>Выберите предмет для просмотра рейтинга:</b>"
    try:
        if callback.message.text:
            await callback.message.edit_text(current_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("subj_select:"))
async def process_subj_select(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    subjects = data.get("cached_subjects")
    
    if not subjects:
        subjects = await get_subjects_with_stats()
        await state.update_data(cached_subjects=subjects)
        
    if not subjects or idx < 0 or idx >= len(subjects):
        await callback.answer("Ошибка: не удалось найти предмет.", show_alert=True)
        return
        
    subject = subjects[idx]
    rating = await get_subject_rating(subject)
    
    if not rating:
        await callback.answer("📭 Данных по этому предмету нет.", show_alert=True)
        return
        
    lines = [f"🏆 <b>Рейтинг по предмету:</b>\n<i>{subject}</i>\n"]
    for i, entry in enumerate(rating, start=1):
        teacher = entry["teacher"]
        passed = entry["passed"]
        total = entry["total"]
        rate = entry["pass_rate"]
        
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔸"
        lines.append(f"{emoji} <b>{i}. {teacher}</b> — {passed}/{total} ({rate}%)")
        
        if i >= 50:
            lines.append("\n<i>... и остальные</i>")
            break
            
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... текст обрезан</i>"
        
    builder = InlineKeyboardBuilder()
    page = idx // 10
    builder.button(text="⬅️ Назад к списку", callback_data=f"subj_page:{page}")
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()
