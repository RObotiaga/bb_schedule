from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from app.core.repositories.subject import get_subjects_with_stats
from app.bot.keyboards import get_subjects_keyboard
from app.bot.states import SubjectSearch
from aiogram.types import InlineKeyboardMarkup

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

@router.callback_query(F.data == "subj_search_start")
async def start_subject_search(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔍 <b>Введите название предмета (или часть названия) для поиска:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="subj_page:0")]
        ]),
        parse_mode="HTML"
    )
    await state.set_state(SubjectSearch.waiting_for_subject_name)
    await callback.answer()

@router.message(SubjectSearch.waiting_for_subject_name)
async def process_subject_search(message: Message, state: FSMContext):
    query = message.text.lower()
    data = await state.get_data()
    subjects = data.get("cached_subjects")
    
    if not subjects:
        subjects = await get_subjects_with_stats()
        await state.update_data(cached_subjects=subjects)
        
    matches = []
    for i, s in enumerate(subjects):
        if query in s.lower():
            matches.append((i, s))
            
    if not matches:
        await message.answer(
            "❌ Предметы по вашему запросу не найдены. Попробуйте другой запрос или вернитесь к списку.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К полному списку", callback_data="subj_page:0")]
            ])
        )
        return
        
    builder = InlineKeyboardBuilder()
    for i, subj in matches[:50]: # Ограничиваем до 50 результатов
        display_text = subj[:40] + "..." if len(subj) > 40 else subj
        builder.button(text=display_text, callback_data=f"subj_select:{i}")
    
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="⬅️ К полному списку", callback_data="subj_page:0"))
    
    await message.answer(
        f"🔍 <b>Результаты поиска по запросу «{message.text}»:</b>\n" + 
        ("<i>Показаны первые 50 совпадений</i>" if len(matches) > 50 else ""), 
        reply_markup=builder.as_markup(), 
        parse_mode="HTML"
    )
    await state.set_state(None)



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
        
    from app.core.repositories.subject import get_global_subject_stats
    subject = subjects[idx]
    stats = await get_global_subject_stats(subject)
    
    if not stats:
        await callback.answer("📭 Данных по этому предмету нет.", show_alert=True)
        return
        
    passed = stats["passed_persons"]
    total = stats["total_persons"]
    rate = stats["person_pass_rate"]
    debts = total - passed
    
    text = (
        f"🏆 <b>Статистика по предмету:</b>\n"
        f"<i>{subject}</i>\n\n"
        f"📈 Успешно сдали: {passed} чел.\n"
        f"📉 Имеют долг: {debts} чел.\n"
        f"📊 Закрываемость: {rate}%"
    )
        
    builder = InlineKeyboardBuilder()
    page = idx // 10
    builder.button(text="⬅️ Назад к списку", callback_data=f"subj_page:{page}")
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()
