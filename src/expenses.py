from __future__ import annotations

from typing import Dict, List, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message,
                           ReplyKeyboardMarkup)

from .dependencies import sheets_service
from .main import (ActiveShift, MainStates, build_keyboard, format_amount,
                   go_to_shift_menu, shift_menu_keyboard)

router = Router()


class ExpensesStates(StatesGroup):
    """Состояния раздела расходов."""

    ship = State()
    holds = State()
    transport = State()
    foreman = State()
    workers = State()
    aux = State()
    food = State()
    taxi = State()
    other = State()
    review = State()


EXPENSES_ORDER = [
    "ship",
    "holds",
    "transport",
    "foreman",
    "workers",
    "aux",
    "food",
    "taxi",
    "other",
]

EXPENSES_LABELS = {
    "ship": "Судно",
    "holds": "Количество трюмов",
    "transport": "Транспортные расходы",
    "foreman": "Оплата бригадира",
    "workers": "Оплата рабочих",
    "aux": "Вспомогательные расходы",
    "food": "Питание",
    "taxi": "Такси",
    "other": "Прочие расходы",
}

EXPENSES_AMOUNT_MESSAGES = {
    "transport": "Укажите сумму затрат на водителя.",
    "foreman": "Укажите сумму вашего вознаграждения.",
    "workers": "Укажите сумму оплаты рабочих.",
    "aux": "Укажите сумму вспомогательных расходов.",
    "food": "Укажите сумму расходов на питание.",
    "taxi": "Укажите сумму расходов на такси.",
    "other": "Укажите сумму прочих расходов.",
}

EXPENSES_STATE_BY_KEY = {
    "ship": ExpensesStates.ship,
    "holds": ExpensesStates.holds,
    "transport": ExpensesStates.transport,
    "foreman": ExpensesStates.foreman,
    "workers": ExpensesStates.workers,
    "aux": ExpensesStates.aux,
    "food": ExpensesStates.food,
    "taxi": ExpensesStates.taxi,
    "other": ExpensesStates.other,
}


async def ensure_expenses_context(state: FSMContext, expenses_row: int) -> Dict[str, str]:
    data = await state.get_data()
    expenses_data: Optional[Dict[str, str]] = data.get("expenses_data")
    if not expenses_data:
        expenses_data = await sheets_service.get_expenses_details(expenses_row)
        for key in EXPENSES_ORDER:
            expenses_data.setdefault(key, "")
        await state.update_data(expenses_data=expenses_data)
    ships_directory: Optional[List[str]] = data.get("ships_directory")
    if ships_directory is None:
        ships_directory = await sheets_service.get_ships_directory()
        await state.update_data(ships_directory=ships_directory)
    return expenses_data


async def prompt_expenses_ship(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    expenses = data.get("expenses_data", {})
    current = expenses.get("ship", "")
    suffix = f"\nТекущие данные: {current}" if current else ""
    await state.set_state(ExpensesStates.ship)
    await message.answer(
        "Введите название судна или его начальные буквы." + suffix,
        reply_markup=build_keyboard([]),
    )


async def prompt_expenses_holds(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    expenses = data.get("expenses_data", {})
    current = expenses.get("holds", "")
    suffix = f"\nТекущие данные: {current}" if current else ""
    number_rows = [["1", "2", "3"], ["4", "5", "6"], ["7"]]
    await state.set_state(ExpensesStates.holds)
    await message.answer(
        "Сколько трюмов на судне?" + suffix,
        reply_markup=build_keyboard(number_rows),
    )


async def prompt_expenses_amount(message: Message, state: FSMContext, key: str) -> None:
    data = await state.get_data()
    expenses = data.get("expenses_data", {})
    current = expenses.get(key, "")
    suffix = f"\nТекущие данные: {current}" if current else ""
    state_obj = EXPENSES_STATE_BY_KEY[key]
    await state.set_state(state_obj)
    await message.answer(
        EXPENSES_AMOUNT_MESSAGES[key] + suffix,
        reply_markup=build_keyboard(include_skip=True),
    )


async def prompt_expenses_by_key(message: Message, state: FSMContext, key: str) -> None:
    if key == "ship":
        await prompt_expenses_ship(message, state)
    elif key == "holds":
        await prompt_expenses_holds(message, state)
    else:
        await prompt_expenses_amount(message, state, key)


async def go_to_previous_expenses_step(message: Message, state: FSMContext, current_key: str) -> None:
    index = EXPENSES_ORDER.index(current_key)
    if index == 0:
        await go_to_shift_menu(message, state)
        return
    previous_key = EXPENSES_ORDER[index - 1]
    await prompt_expenses_by_key(message, state, previous_key)


async def go_to_next_expenses_step(message: Message, state: FSMContext, current_key: str) -> None:
    index = EXPENSES_ORDER.index(current_key)
    if index == len(EXPENSES_ORDER) - 1:
        await show_expenses_review(message, state)
        return
    next_key = EXPENSES_ORDER[index + 1]
    await prompt_expenses_by_key(message, state, next_key)


async def show_expenses_review(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    expenses: Dict[str, str] = data.get("expenses_data", {})
    lines = []
    for key in EXPENSES_ORDER:
        raw_value = expenses.get(key, "") or ""
        if key in EXPENSES_ORDER[2:]:
            display = format_amount(raw_value) if raw_value else "0"
        else:
            display = raw_value or "—"
        lines.append(f"{EXPENSES_LABELS[key]}: {display}")
    total = 0
    for key in EXPENSES_ORDER[2:]:
        raw = expenses.get(key, "")
        cleaned = raw.replace(" ", "") if isinstance(raw, str) else ""
        if cleaned.isdigit():
            total += int(cleaned)
    review_text = (
        "Проверьте введённые данные по смене. Подтвердите или отредактируйте."
        f"\n\n" + "\n".join(lines)
        + f"\n\nИтого расходов: {total} ₽."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data="expenses:confirm")],
            [InlineKeyboardButton(text="Изменить", callback_data="expenses:edit")],
        ]
    )
    await state.set_state(ExpensesStates.review)
    await message.answer(review_text, reply_markup=keyboard)


@router.callback_query(F.data == "shift_section:expenses", MainStates.shift_menu)
async def start_expenses(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена не найдена", show_alert=True)
        return
    expenses_row = active_shift.rows.get("expenses")
    if not expenses_row:
        await callback.answer("Строка расходов не найдена", show_alert=True)
        return
    await ensure_expenses_context(state, expenses_row)
    await prompt_expenses_ship(callback.message, state)
    await callback.answer()


@router.message(ExpensesStates.ship)
async def expenses_ship_input(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    lower = text.lower()
    if lower == "в меню":
        await go_to_shift_menu(message, state)
        return
    if lower == "⬅ назад":
        await go_to_shift_menu(message, state)
        return
    if not text:
        await message.answer("Введите название судна или выберите из списка.")
        return

    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await message.answer("Активная смена не найдена.")
        return
    expenses_row = active_shift.rows.get("expenses")
    if not expenses_row:
        await message.answer("Строка расходов не найдена.")
        return
    expenses = await ensure_expenses_context(state, expenses_row)
    ships_directory: List[str] = data.get("ships_directory", []) or []

    chosen_ship = text
    if ships_directory:
        matches = [ship for ship in ships_directory if ship.lower().startswith(lower)]
        if text in ships_directory:
            chosen_ship = text
        elif len(matches) == 1:
            chosen_ship = matches[0]
        elif len(matches) > 1:
            suggestion_rows = [[name] for name in matches[:6]]
            await message.answer(
                "Найдено несколько совпадений. Выберите судно из списка.",
                reply_markup=build_keyboard(suggestion_rows),
            )
            return
        elif lower and not matches:
            await message.answer(
                "Судно не найдено в справочнике. Выберите из списка или введите корректное имя.",
                reply_markup=build_keyboard([[ship] for ship in ships_directory[:6]]),
            )
            return

    expenses["ship"] = chosen_ship
    await state.update_data(expenses_data=expenses)
    await go_to_next_expenses_step(message, state, "ship")


@router.message(ExpensesStates.holds)
async def expenses_holds_input(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    lower = text.lower()
    if lower == "в меню":
        await go_to_shift_menu(message, state)
        return
    if lower == "⬅ назад":
        await go_to_previous_expenses_step(message, state, "holds")
        return
    if not text.isdigit() or int(text) < 1 or int(text) > 7:
        await message.answer("Пожалуйста, выберите значение от 1 до 7.")
        return

    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await message.answer("Активная смена не найдена.")
        return
    expenses_row = active_shift.rows.get("expenses")
    if not expenses_row:
        await message.answer("Строка расходов не найдена.")
        return
    expenses = await ensure_expenses_context(state, expenses_row)
    expenses["holds"] = text
    await state.update_data(expenses_data=expenses)
    await message.answer(f"Количество трюмов зафиксировано: {text}.")
    await go_to_next_expenses_step(message, state, "holds")


async def process_expense_amount(message: Message, state: FSMContext, key: str) -> None:
    text = (message.text or "").strip()
    lower = text.lower()
    if lower == "в меню":
        await go_to_shift_menu(message, state)
        return
    if lower == "⬅ назад":
        await go_to_previous_expenses_step(message, state, key)
        return
    if lower == "пропустить":
        value = "0"
    else:
        cleaned = text.replace(" ", "")
        if not cleaned.isdigit():
            await message.answer("Введите сумму цифрами или воспользуйтесь кнопкой «Пропустить».")
            return
        value = cleaned

    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await message.answer("Активная смена не найдена.")
        return
    expenses_row = active_shift.rows.get("expenses")
    if not expenses_row:
        await message.answer("Строка расходов не найдена.")
        return
    expenses = await ensure_expenses_context(state, expenses_row)
    expenses[key] = value
    await state.update_data(expenses_data=expenses)
    formatted = format_amount(value) if value else "0"
    await message.answer(f"{EXPENSES_LABELS[key]} зафиксирована: {formatted} ₽.")
    await go_to_next_expenses_step(message, state, key)


@router.message(ExpensesStates.transport)
async def expenses_transport_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "transport")


@router.message(ExpensesStates.foreman)
async def expenses_foreman_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "foreman")


@router.message(ExpensesStates.workers)
async def expenses_workers_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "workers")


@router.message(ExpensesStates.aux)
async def expenses_aux_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "aux")


@router.message(ExpensesStates.food)
async def expenses_food_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "food")


@router.message(ExpensesStates.taxi)
async def expenses_taxi_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "taxi")


@router.message(ExpensesStates.other)
async def expenses_other_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "other")


@router.message(ExpensesStates.review)
async def expenses_review_navigation(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    if text == "в меню":
        await go_to_shift_menu(message, state)
        return
    if text == "⬅ назад":
        await go_to_previous_expenses_step(message, state, "other")
        return
    await message.answer("Для завершения используйте кнопки подтверждения ниже.")


@router.callback_query(F.data == "expenses:edit", ExpensesStates.review)
async def expenses_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await prompt_expenses_ship(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "expenses:confirm", ExpensesStates.review)
async def expenses_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена не найдена", show_alert=True)
        return
    expenses_row = active_shift.rows.get("expenses")
    if not expenses_row:
        await callback.answer("Строка расходов не найдена", show_alert=True)
        return
    expenses: Dict[str, str] = data.get("expenses_data", {})
    await sheets_service.save_expenses_details(expenses_row, expenses)
    active_shift.sections["expenses"] = True
    await state.update_data(active_shift=active_shift)
    await state.set_state(MainStates.shift_menu)
    await callback.message.answer(
        "Раздел «Расходы смены» сохранён.",
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )
    await callback.answer("Расходы сохранены")

