"""Сценарий раздела «Расходы смены» с очисткой истории сообщений."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.expenses import (
    CONFIRM_BUTTON,
    EDIT_BUTTON,
    MENU_BUTTON,
    SKIP_BUTTON,
    START_EXPENSES_BUTTON,
    expenses_amount_keyboard,
    expenses_confirm_keyboard,
    expenses_holds_keyboard,
    expenses_remove_keyboard,
    expenses_ship_keyboard,
    expenses_start_keyboard,
)
from bot.utils.flash import flash_message
from bot.validators.number import parse_amount
from features.utils.messaging import safe_delete
from services.sheets import SheetsService

router = Router(name="expenses")

logger = logging.getLogger(__name__)
_service: SheetsService | None = None

SHIP_PATTERN = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\- ]{1,49}$")


class ExpensesState(StatesGroup):
    """Этапы сценария «Расходы смены»"""

    INTRO = State()
    SHIP = State()
    HOLDS = State()
    DRIVER = State()
    BRIGADIER = State()
    WORKERS = State()
    AUX = State()
    FOOD = State()
    TAXI = State()
    OTHER = State()
    CONFIRM = State()


async def _delete_messages(bot: types.Bot, chat_id: int, message_ids: list[int]) -> None:
    """Безопасно удаляет набор сообщений."""

    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            continue
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось удалить сообщение %s", message_id)


def _get_service() -> SheetsService:
    """Возвращает (или создаёт) общий экземпляр SheetsService."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


def _make_tracker() -> Dict[str, Any]:
    """Создаёт заготовку трекера сообщений."""

    return {"prompt_id": None, "user_messages": [], "bot_messages": []}


async def _get_context(state: FSMContext) -> Dict[str, Any]:
    """Читает контекст раздела расходов из FSM."""

    data = await state.get_data()
    context = data.get("expenses_ctx")
    if not isinstance(context, dict):
        context = {}
    context.setdefault("data", {})
    tracker = context.get("tracker")
    if not isinstance(tracker, dict):
        tracker = _make_tracker()
    else:
        tracker.setdefault("prompt_id", None)
        tracker.setdefault("user_messages", [])
        tracker.setdefault("bot_messages", [])
    context["tracker"] = tracker
    return context


async def _save_context(state: FSMContext, context: Dict[str, Any]) -> None:
    """Сохраняет контекст раздела расходов в FSM."""

    await state.update_data(expenses_ctx=context)


def _normalize_ship_name(value: str) -> str:
    """Приводит название судна к аккуратному виду."""

    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned:
        return cleaned
    return cleaned[0].upper() + cleaned[1:]


async def _set_prompt(
    message: types.Message,
    state: FSMContext,
    *,
    prompt: types.Message,
) -> None:
    """Фиксирует новое сообщение-приглашение для удаления после шага."""

    context = await _get_context(state)
    tracker = _make_tracker()
    tracker["prompt_id"] = prompt.message_id
    context["tracker"] = tracker
    await _save_context(state, context)


async def _add_user_message(state: FSMContext, message_id: int) -> None:
    """Запоминает сообщение пользователя для последующего удаления."""

    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    tracker.setdefault("user_messages", []).append(message_id)
    context["tracker"] = tracker
    await _save_context(state, context)


async def _add_bot_message(state: FSMContext, message_id: int) -> None:
    """Запоминает вспомогательное сообщение бота для очистки."""

    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    tracker.setdefault("bot_messages", []).append(message_id)
    context["tracker"] = tracker
    await _save_context(state, context)


async def _cleanup_step(message: types.Message, state: FSMContext) -> None:
    """Удаляет сообщения текущего шага и сбрасывает трекер."""

    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    bot = message.bot
    chat_id = message.chat.id
    ids: List[int] = []
    prompt_id = tracker.get("prompt_id")
    if prompt_id:
        ids.append(prompt_id)
    ids.extend(tracker.get("bot_messages", []))
    await _delete_messages(bot, chat_id, ids)
    await _delete_messages(bot, chat_id, tracker.get("user_messages", []))
    context["tracker"] = _make_tracker()
    await _save_context(state, context)


async def _return_to_menu(message: types.Message, state: FSMContext) -> None:
    """Возвращает пользователя в меню смены без сохранения данных."""

    context = await _get_context(state)
    user_id = context.get("user_id")
    row = context.get("row")
    await _cleanup_step(message, state)
    if not isinstance(user_id, int) or not isinstance(row, int):
        return
    await state.update_data(expenses_ctx=None)
    from bot.handlers.shift_menu import render_shift_menu  # локальный импорт, чтобы избежать цикла

    await render_shift_menu(
        message,
        user_id,
        row,
        state=state,
        service=_get_service(),
        delete_trigger_message=False,
        show_progress=False,
    )


async def start_expenses(
    message: types.Message,
    state: FSMContext,
    *,
    user_id: int | None = None,
) -> None:
    """Точка входа в раздел «Расходы смены» из меню смены."""

    await safe_delete(message)
    actual_user_id = user_id or (message.from_user.id if message.from_user else None)
    if actual_user_id is None:
        await message.answer(
            "Не удалось определить пользователя. Начните смену заново через главное меню."
        )
        await state.update_data(expenses_ctx=None)
        return

    service = _get_service()
    row = await asyncio.to_thread(service.get_shift_row_index_for_user, actual_user_id)
    if row is None:
        row = await asyncio.to_thread(service.open_shift_for_user, actual_user_id)

    intro_lines = [
        "🧾 Раздел «Расходы смены»",
        "Здесь фиксируем расходы по категориям. Нажмите кнопку ниже, чтобы начать ввод.",
    ]
    prompt = await message.answer("\n".join(intro_lines), reply_markup=expenses_start_keyboard())

    tracker = _make_tracker()
    tracker["prompt_id"] = prompt.message_id
    context = {
        "user_id": actual_user_id,
        "row": row,
        "data": {},
        "ships": [],
        "tracker": tracker,
    }
    await _save_context(state, context)
    await state.set_state(ExpensesState.INTRO)


async def _ask_ship(message: types.Message, state: FSMContext) -> None:
    """Запрашивает название судна и показывает подсказки."""

    context = await _get_context(state)
    service = _get_service()
    ships = await asyncio.to_thread(service.get_active_ships)
    context["ships"] = ships
    await _save_context(state, context)

    if ships:
        text = (
            "выберите судно из списка ниже или начните ввод названия.\n"
            "если нужного судна нет, бот добавит его в таблицу."
        )
        keyboard = expenses_ship_keyboard(ships[:8])
    else:
        text = (
            "введите название судна. если его нет в списке, бот предложит добавить новое."
        )
        keyboard = expenses_ship_keyboard([])

    prompt = await message.answer(text, reply_markup=keyboard)
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(ExpensesState.SHIP)


async def _ask_holds(message: types.Message, state: FSMContext) -> None:
    """Переходит к шагу выбора числа трюмов."""

    prompt = await message.answer(
        "сколько трюмов на судне?\nвыберите число от 1 до 7 на клавиатуре ниже.",
        reply_markup=expenses_holds_keyboard(),
    )
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(ExpensesState.HOLDS)


async def _ask_amount(
    message: types.Message,
    state: FSMContext,
    *,
    step: ExpensesState,
    question: str,
) -> None:
    """Запрашивает сумму расходов для указанного шага."""

    prompt = await message.answer(
        f"{question}\nтолько цифры (рубли) или «Пропустить».",
        reply_markup=expenses_amount_keyboard(include_skip=True),
    )
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(step)


async def _ask_confirm(message: types.Message, state: FSMContext) -> None:
    """Формирует экран подтверждения с итоговыми значениями."""

    context = await _get_context(state)
    data = context.get("data", {})
    total = sum(int(data.get(key, 0) or 0) for key in ("e", "f", "g", "h", "i", "j", "k"))
    data["total"] = total
    text = (
        "проверьте введённые данные:\n"
        f"• судно: {data.get('ship', '—')}\n"
        f"• трюмов: {data.get('holds', '—')}\n"
        f"• транспорт: {data.get('e', 0)}\n"
        f"• бригадир: {data.get('f', 0)}\n"
        f"• рабочие: {data.get('g', 0)}\n"
        f"• вспомогательные: {data.get('h', 0)}\n"
        f"• питание: {data.get('i', 0)}\n"
        f"• такси: {data.get('j', 0)}\n"
        f"• прочие: {data.get('k', 0)}\n"
        f"— всего расходов: {total}"
    )
    prompt = await message.answer(text, reply_markup=expenses_confirm_keyboard())
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(ExpensesState.CONFIRM)


async def _handle_menu_button(message: types.Message, state: FSMContext) -> bool:
    """Проверяет нажатие кнопки возврата и выполняет выход, если нужно."""

    if message.text == MENU_BUTTON:
        await _add_user_message(state, message.message_id)
        await _return_to_menu(message, state)
        return True
    return False


@router.message(ExpensesState.INTRO)
async def handle_intro(message: types.Message, state: FSMContext) -> None:
    """Обработчик стартового экрана раздела."""

    if await _handle_menu_button(message, state):
        return

    if message.text != START_EXPENSES_BUTTON:
        hint = await message.answer("Используйте кнопку «🧾 Начать ввод расходов».")
        await _add_bot_message(state, hint.message_id)
        await _add_user_message(state, message.message_id)
        return

    await _add_user_message(state, message.message_id)
    await _cleanup_step(message, state)
    await _ask_ship(message, state)


@router.message(ExpensesState.SHIP)
async def handle_ship(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает выбор судна."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id)
    context = await _get_context(state)
    ships: list[str] = context.get("ships", [])
    text = (message.text or "").strip()
    if not text:
        reply = await message.answer("повторите ввод названия судна.")
        await _add_bot_message(state, reply.message_id)
        return

    ships_map = {name.casefold(): name for name in ships}
    lookup = ships_map.get(text.casefold())
    if lookup:
        normalized = lookup
    else:
        if not SHIP_PATTERN.fullmatch(text):
            reply = await message.answer(
                "название выглядит некорректно. используйте буквы/цифры/дефис/пробел."
            )
            await _add_bot_message(state, reply.message_id)
            return
        normalized = _normalize_ship_name(text)
        service = _get_service()
        await asyncio.to_thread(service.add_ship, normalized)
        ships.append(normalized)
        context["ships"] = ships

    data = context.setdefault("data", {})
    data["ship"] = normalized
    await _save_context(state, context)
    await _cleanup_step(message, state)
    await _ask_holds(message, state)


@router.message(ExpensesState.HOLDS)
async def handle_holds(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает выбор числа трюмов."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id)
    text = (message.text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= 7):
        reply = await message.answer("выберите число от 1 до 7 на клавиатуре ниже.")
        await _add_bot_message(state, reply.message_id)
        return

    context = await _get_context(state)
    context.setdefault("data", {})["holds"] = int(text)
    await _save_context(state, context)
    await _cleanup_step(message, state)
    await _ask_amount(
        message,
        state,
        step=ExpensesState.DRIVER,
        question="укажите сумму затрат на водителя.",
    )


async def _handle_amount(
    message: types.Message,
    state: FSMContext,
    *,
    key: str,
    next_step: ExpensesState,
    question: str,
) -> None:
    """Общая логика обработки денежных шагов."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id)
    try:
        amount = parse_amount(message.text or "", skip_token=SKIP_BUTTON)
    except ValueError:
        reply = await message.answer("только цифры (рубли) или «Пропустить».")
        await _add_bot_message(state, reply.message_id)
        return

    context = await _get_context(state)
    context.setdefault("data", {})[key] = amount
    await _save_context(state, context)
    await _cleanup_step(message, state)
    await _ask_amount(message, state, step=next_step, question=question)


@router.message(ExpensesState.DRIVER)
async def handle_driver(message: types.Message, state: FSMContext) -> None:
    await _handle_amount(
        message,
        state,
        key="e",
        next_step=ExpensesState.BRIGADIER,
        question="укажите сумму вашего вознаграждения (бригадир).",
    )


@router.message(ExpensesState.BRIGADIER)
async def handle_brigadier(message: types.Message, state: FSMContext) -> None:
    await _handle_amount(
        message,
        state,
        key="f",
        next_step=ExpensesState.WORKERS,
        question="укажите сумму оплаты рабочих.",
    )


@router.message(ExpensesState.WORKERS)
async def handle_workers(message: types.Message, state: FSMContext) -> None:
    await _handle_amount(
        message,
        state,
        key="g",
        next_step=ExpensesState.AUX,
        question="укажите сумму вспомогательных расходов.",
    )


@router.message(ExpensesState.AUX)
async def handle_aux(message: types.Message, state: FSMContext) -> None:
    await _handle_amount(
        message,
        state,
        key="h",
        next_step=ExpensesState.FOOD,
        question="укажите сумму расходов на питание.",
    )


@router.message(ExpensesState.FOOD)
async def handle_food(message: types.Message, state: FSMContext) -> None:
    await _handle_amount(
        message,
        state,
        key="i",
        next_step=ExpensesState.TAXI,
        question="укажите сумму расходов на такси.",
    )


@router.message(ExpensesState.TAXI)
async def handle_taxi(message: types.Message, state: FSMContext) -> None:
    await _handle_amount(
        message,
        state,
        key="j",
        next_step=ExpensesState.OTHER,
        question="укажите сумму прочих расходов.",
    )


@router.message(ExpensesState.OTHER)
async def handle_other(message: types.Message, state: FSMContext) -> None:
    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id)
    try:
        amount = parse_amount(message.text or "", skip_token=SKIP_BUTTON)
    except ValueError:
        reply = await message.answer("только цифры (рубли) или «Пропустить».")
        await _add_bot_message(state, reply.message_id)
        return

    context = await _get_context(state)
    context.setdefault("data", {})["k"] = amount
    await _save_context(state, context)
    await _cleanup_step(message, state)
    await _ask_confirm(message, state)


@router.message(ExpensesState.CONFIRM)
async def handle_confirm(message: types.Message, state: FSMContext) -> None:
    """Подтверждение или повторный ввод данных."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id)
    text = message.text or ""
    if text == EDIT_BUTTON:
        context = await _get_context(state)
        context["data"] = {}
        await _save_context(state, context)
        await _cleanup_step(message, state)
        await _ask_ship(message, state)
        return
    if text != CONFIRM_BUTTON:
        reply = await message.answer("Используйте кнопки подтверждения на клавиатуре.")
        await _add_bot_message(state, reply.message_id)
        return

    await _cleanup_step(message, state)
    await flash_message(message, "💾 Сохраняю…", ttl=2.0)
    context = await _get_context(state)
    data = context.get("data", {})
    user_id = context.get("user_id")
    row = context.get("row")
    if not isinstance(user_id, int) or not isinstance(row, int):
        await message.answer("Не удалось сохранить данные: нет привязки к смене.")
        await state.update_data(expenses_ctx=None)
        return

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
    )

    from bot.handlers.shift_menu import mark_mode_done, render_shift_menu

    mark_mode_done(user_id, "expenses")
    done_message = await message.answer(
        "раздел «расходы смены» сохранён ✅",
        reply_markup=expenses_remove_keyboard(),
    )
    await state.update_data(expenses_ctx=None)
    await render_shift_menu(
        message,
        user_id,
        row,
        state=state,
        service=service,
        delete_trigger_message=False,
        show_progress=False,
    )
    if done_message:
        try:
            await message.bot.delete_message(message.chat.id, done_message.message_id)
        except TelegramBadRequest:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось удалить итоговое сообщение расходов")
