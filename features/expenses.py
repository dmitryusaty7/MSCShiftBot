"""Сценарий заполнения раздела «Расходы смены» по шагам."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from features.utils.messaging import safe_delete
from services.sheets import SheetsService

if TYPE_CHECKING:  # pragma: no cover
    from features.shift_menu import render_shift_menu as RenderShiftMenuFn

router = Router()
_service: SheetsService | None = None


def _get_service() -> SheetsService:
    """Ленивая инициализация сервиса работы с таблицей."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


def _render_shift_menu(*args, **kwargs):
    """Ленивый импорт меню смены для избежания циклических зависимостей."""

    from features.shift_menu import render_shift_menu

    return render_shift_menu(*args, **kwargs)

BTN_BACK = "⬅ Назад"
BTN_HOME = "🏠 В меню"
BTN_SKIP = "Пропустить"

HOLDS_CHOICES = [str(i) for i in range(1, 8)]


def normalize_ship_name(value: str) -> str:
    """Приводит название судна к аккуратному виду с заглавной буквой."""

    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned:
        return cleaned
    return cleaned[0].upper() + cleaned[1:].lower()


def numeric_or_zero(text: str) -> int | None:
    """Возвращает число из строки или 0 для «Пропустить»; иначе None."""

    candidate = text.strip()
    if candidate == BTN_SKIP:
        return 0
    if not re.fullmatch(r"\d+", candidate):
        return None
    return int(candidate)


class ExpenseFSM(StatesGroup):
    """Шаги сценария заполнения расходов."""

    ship = State()
    holds = State()
    e = State()
    f = State()
    g = State()
    h = State()
    i = State()
    j = State()
    k = State()
    confirm = State()


def nav_kb(extra: list[str] | None = None) -> types.ReplyKeyboardMarkup:
    """Формирует клавиатуру навигации по сценарию."""

    keyboard = ReplyKeyboardBuilder()
    if extra:
        for value in extra:
            keyboard.button(text=value)
        keyboard.adjust(len(extra))
    keyboard.button(text=BTN_BACK)
    keyboard.button(text=BTN_HOME)
    keyboard.adjust(2)
    return keyboard.as_markup(resize_keyboard=True)


def holds_kb() -> types.ReplyKeyboardMarkup:
    """Клавиатура выбора числа трюмов."""

    keyboard = ReplyKeyboardBuilder()
    for number in HOLDS_CHOICES:
        keyboard.button(text=number)
    keyboard.adjust(7)
    keyboard.button(text=BTN_BACK)
    keyboard.button(text=BTN_HOME)
    keyboard.adjust(7, 2)
    return keyboard.as_markup(resize_keyboard=True)


def ship_choices_kb(ships: list[str]) -> types.ReplyKeyboardMarkup:
    """Клавиатура с подсказками судов и кнопками навигации."""

    keyboard = ReplyKeyboardBuilder()
    for name in ships:
        keyboard.button(text=name)
    keyboard.button(text=BTN_BACK)
    keyboard.button(text=BTN_HOME)
    row_sizes = [1] * len(ships)
    row_sizes.append(2)
    keyboard.adjust(*row_sizes)
    return keyboard.as_markup(resize_keyboard=True)


@router.message(Command("expenses"))
async def start_expenses(message: types.Message, state: FSMContext) -> None:
    """Запускает сценарий заполнения раздела расходов."""

    await safe_delete(message)
    user_id = message.from_user.id
    service = _get_service()
    row = await asyncio.to_thread(
        service.get_shift_row_index_for_user, user_id
    )
    if row is None:
        row = await asyncio.to_thread(service.open_shift_for_user, user_id)
    await state.update_data(row=row, user_id=user_id)
    await ask_ship(message, state)


async def ask_ship(message: types.Message, state: FSMContext) -> None:
    """Запрашивает название судна и предлагает варианты из списка."""

    service = _get_service()
    ships = await asyncio.to_thread(service.get_active_ships)
    await state.update_data(_ships=ships, _candidate=None)
    await state.set_state(ExpenseFSM.ship)
    suggestions = ships[:10]
    if suggestions:
        prompt = (
            "выберите судно из списка ниже или начните ввод названия.\n"
            "если нужного судна нет, бот предложит добавить его в таблицу."
        )
        markup = ship_choices_kb(suggestions)
    else:
        prompt = (
            "введите название судна. если его нет в списке,"
            " бот предложит добавить новое."
        )
        markup = nav_kb()
    await message.answer(prompt, reply_markup=markup)


@router.message(ExpenseFSM.ship)
async def ship_input(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод названия судна и выбор из предложенных вариантов."""

    text = message.text.strip()
    if text.startswith("Добавить: "):
        # Обработка нажатия кнопки «Добавить: …» должна сработать даже при
        # повторном вызове базового хэндлера из-за порядка регистрации.
        return await add_ship(message, state)
    if text in (BTN_BACK, BTN_HOME):
        await safe_delete(message)
        return await exit_by_nav(message, state, text)

    data = await state.get_data()
    ships: list[str] = data.get("_ships", [])
    query = text.lower()
    matches = [name for name in ships if query in name.lower()]

    if len(matches) == 1:
        await state.update_data(ship=matches[0])
        await safe_delete(message)
        return await ask_holds(message, state)

    if len(matches) > 1:
        keyboard = ship_choices_kb(matches[:10])
        await message.answer(
            "нашлось несколько вариантов, выберите:",
            reply_markup=keyboard,
        )
        return

    candidate = normalize_ship_name(text)
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9\- ]{2,}", candidate):
        await message.answer(
            "название выглядит некорректно. используйте буквы/цифры/дефис/пробел."
        )
        return

    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text=f"Добавить: {candidate}")
    keyboard.adjust(1)
    keyboard.button(text=BTN_BACK)
    keyboard.button(text=BTN_HOME)
    keyboard.adjust(1, 2)
    await state.update_data(_candidate=candidate)
    await message.answer(
        f"судно не найдено. добавить «{candidate}» как новое?",
        reply_markup=keyboard.as_markup(resize_keyboard=True),
    )


@router.message(ExpenseFSM.ship, F.text.startswith("Добавить: "))
async def add_ship(message: types.Message, state: FSMContext) -> None:
    """Добавляет новое судно в справочник и переходит к следующему шагу."""

    data = await state.get_data()
    candidate = data.get("_candidate")
    if not candidate:
        await message.answer("повторите ввод названия судна.")
        return
    service = _get_service()
    await asyncio.to_thread(service.add_ship, candidate)
    await state.update_data(ship=candidate)
    await safe_delete(message)
    await ask_holds(message, state)


async def ask_holds(message: types.Message, state: FSMContext) -> None:
    """Запрашивает количество трюмов."""

    await state.set_state(ExpenseFSM.holds)
    await message.answer(
        "сколько трюмов на судне?",
        reply_markup=holds_kb(),
    )


@router.message(ExpenseFSM.holds)
async def holds_input(message: types.Message, state: FSMContext) -> None:
    """Получает количество трюмов и переходит к денежным полям."""

    text = message.text.strip()
    if text in (BTN_BACK, BTN_HOME):
        await safe_delete(message)
        return await exit_by_nav(message, state, text)
    if text not in HOLDS_CHOICES:
        await message.answer("выберите число от 1 до 7 на клавиатуре ниже.")
        return
    await state.update_data(holds=int(text))
    await safe_delete(message)
    await ask_amount(
        message,
        state,
        ExpenseFSM.e,
        "укажите сумму затрат на водителя.",
        allow_skip=True,
    )


async def ask_amount(
    message: types.Message,
    state: FSMContext,
    next_state: State,
    prompt: str,
    *,
    allow_skip: bool,
) -> None:
    """Универсальный запрос суммы для одного из полей расходов."""

    await state.set_state(next_state)
    extras = [BTN_SKIP] if allow_skip else []
    await message.answer(prompt, reply_markup=nav_kb(extras))


@router.message(ExpenseFSM.e)
async def e_input(message: types.Message, state: FSMContext) -> None:
    await handle_amount_input(
        message,
        state,
        field_key="e",
        next_state=ExpenseFSM.f,
        next_prompt="укажите сумму вашего вознаграждения (бригадир).",
        allow_skip=True,
    )


@router.message(ExpenseFSM.f)
async def f_input(message: types.Message, state: FSMContext) -> None:
    await handle_amount_input(
        message,
        state,
        field_key="f",
        next_state=ExpenseFSM.g,
        next_prompt="укажите сумму оплаты рабочих.",
        allow_skip=True,
    )


@router.message(ExpenseFSM.g)
async def g_input(message: types.Message, state: FSMContext) -> None:
    await handle_amount_input(
        message,
        state,
        field_key="g",
        next_state=ExpenseFSM.h,
        next_prompt="укажите сумму вспомогательных расходов.",
        allow_skip=True,
    )


@router.message(ExpenseFSM.h)
async def h_input(message: types.Message, state: FSMContext) -> None:
    await handle_amount_input(
        message,
        state,
        field_key="h",
        next_state=ExpenseFSM.i,
        next_prompt="укажите сумму расходов на питание.",
        allow_skip=True,
    )


@router.message(ExpenseFSM.i)
async def i_input(message: types.Message, state: FSMContext) -> None:
    await handle_amount_input(
        message,
        state,
        field_key="i",
        next_state=ExpenseFSM.j,
        next_prompt="укажите сумму расходов на такси.",
        allow_skip=True,
    )


@router.message(ExpenseFSM.j)
async def j_input(message: types.Message, state: FSMContext) -> None:
    await handle_amount_input(
        message,
        state,
        field_key="j",
        next_state=ExpenseFSM.k,
        next_prompt="укажите сумму прочих расходов.",
        allow_skip=True,
    )


@router.message(ExpenseFSM.k)
async def k_input(message: types.Message, state: FSMContext) -> None:
    value = numeric_or_zero(message.text)
    if value is None:
        if message.text in (BTN_BACK, BTN_HOME):
            await safe_delete(message)
            return await exit_by_nav(message, state, message.text)
        await message.answer("только цифры (рубли) или «Пропустить».")
        return
    await state.update_data(k=value)
    await safe_delete(message)
    await show_confirm(message, state)


async def handle_amount_input(
    message: types.Message,
    state: FSMContext,
    *,
    field_key: str,
    next_state: State,
    next_prompt: str,
    allow_skip: bool,
) -> None:
    """Общая обработка числовых полей расходов."""

    value = numeric_or_zero(message.text)
    if value is None:
        if message.text in (BTN_BACK, BTN_HOME):
            await safe_delete(message)
            return await exit_by_nav(message, state, message.text)
        await message.answer("только цифры (рубли) или «Пропустить».")
        return

    await state.update_data(**{field_key: value})
    await safe_delete(message)
    await ask_amount(
        message,
        state,
        next_state,
        next_prompt,
        allow_skip=allow_skip,
    )


async def show_confirm(message: types.Message, state: FSMContext) -> None:
    """Показывает введённые значения и спрашивает подтверждение."""

    await state.set_state(ExpenseFSM.confirm)
    data = await state.get_data()
    e = data.get("e", 0)
    f = data.get("f", 0)
    g = data.get("g", 0)
    h_val = data.get("h", 0)
    i_val = data.get("i", 0)
    j_val = data.get("j", 0)
    k_val = data.get("k", 0)
    total = sum([e, f, g, h_val, i_val, j_val, k_val])
    await state.update_data(total=total)

    ship = data.get("ship")
    holds = data.get("holds")
    text = (
        "проверьте введённые данные:\n"
        f"• судно: {ship}\n"
        f"• трюмов: {holds}\n"
        f"• транспорт: {e}\n"
        f"• бригадир: {f}\n"
        f"• рабочие: {g}\n"
        f"• вспомогательные: {h_val}\n"
        f"• питание: {i_val}\n"
        f"• такси: {j_val}\n"
        f"• прочие: {k_val}\n"
        f"— всего расходов: {total}"
    )
    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text="✅ Подтвердить")
    keyboard.button(text="✏️ Изменить")
    keyboard.adjust(2)
    keyboard.button(text=BTN_BACK)
    keyboard.button(text=BTN_HOME)
    keyboard.adjust(2, 2)
    await message.answer(
        text,
        reply_markup=keyboard.as_markup(resize_keyboard=True),
    )


@router.message(ExpenseFSM.confirm, F.text == "✏️ Изменить")
async def edit_again(message: types.Message, state: FSMContext) -> None:
    """Возвращает пользователя к началу сценария для исправлений."""

    await safe_delete(message)
    await ask_ship(message, state)


@router.message(ExpenseFSM.confirm, F.text == "✅ Подтвердить")
async def confirm_save(message: types.Message, state: FSMContext) -> None:
    """Сохраняет введённые данные и возвращает пользователя в меню смены."""

    await safe_delete(message)
    data = await state.get_data()
    row = data["row"]
    user_id = data["user_id"]
    service = _get_service()
    await asyncio.to_thread(
        service.save_expenses_block,
        user_id,
        row,
        ship=data.get("ship"),
        holds=data.get("holds"),
        e=data.get("e", 0),
        f=data.get("f", 0),
        g=data.get("g", 0),
        h=data.get("h", 0),
        i=data.get("i", 0),
        j=data.get("j", 0),
        k=data.get("k", 0),
        total=data.get("total", 0),
    )
    await state.clear()
    await message.answer("раздел «расходы смены» сохранён ✅")
    await _render_shift_menu(message, user_id, row)


async def exit_by_nav(message: types.Message, state: FSMContext, key: str) -> None:
    """Реакция на навигационные кнопки сценария."""

    data = await state.get_data()
    await state.clear()
    if key == BTN_HOME:
        from features.main_menu import show_menu

        await show_menu(message)
        return
    await _render_shift_menu(message, data.get("user_id"), data.get("row"))
