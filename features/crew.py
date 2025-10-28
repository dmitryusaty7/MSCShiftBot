"""Сценарий заполнения раздела «Бригада» с выбором водителя и рабочих."""

from __future__ import annotations

import asyncio
import logging
from math import ceil
from typing import Any

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from features.utils.messaging import safe_delete
from services.sheets import (
    SheetsService,
    format_compact_fio,
    validate_name_piece,
)

router = Router()
logger = logging.getLogger(__name__)
_service: SheetsService | None = None

BTN_BACK = "⬅ назад"
BTN_HOME = "🏠 в меню"
BTN_ADD_DRIVER = "➕ добавить водителя"
BTN_ADD_WORKER = "➕ добавить рабочего"
BTN_PAGE_PREV = "‹ предыдущая"
BTN_PAGE_NEXT = "следующая ›"
BTN_CONFIRM = "✅ подтвердить"
BTN_EDIT = "✏️ изменить"
BTN_CLEAR_WORKERS = "🧹 очистить список рабочих"
BTN_SKIP = "Пропустить"
BTN_NEXT = "➡ далее"
PAGE_SIZE = 25
REMOVE_PREFIX = "crew:del:"


class CrewStates(StatesGroup):
    """Шаги сценария выбора состава бригады."""

    choose_driver = State()
    add_driver_lastname = State()
    add_driver_firstname = State()
    add_driver_middlename = State()
    choose_workers = State()
    add_worker_lastname = State()
    add_worker_firstname = State()
    add_worker_middlename = State()
    confirm = State()


def _get_service() -> SheetsService:
    """Возвращает лениво инициализированный сервис Google Sheets."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


def _render_shift_menu(*args: Any, **kwargs: Any) -> Any:
    """Ленивый импорт меню смены во избежание циклических зависимостей."""

    from features.shift_menu import render_shift_menu

    return render_shift_menu(*args, **kwargs)


def _nav_keyboard(extra: list[str] | None = None) -> types.ReplyKeyboardMarkup:
    """Формирует клавиатуру с навигационными кнопками."""

    builder = ReplyKeyboardBuilder()
    if extra:
        builder.row(*(types.KeyboardButton(text=item) for item in extra))
    builder.row(
        types.KeyboardButton(text=BTN_BACK),
        types.KeyboardButton(text=BTN_HOME),
    )
    return builder.as_markup(resize_keyboard=True)


def _paginate(items: list[str], page: int) -> tuple[list[str], int, int]:
    """Возвращает элементы текущей страницы и параметры пагинации."""

    if not items:
        return [], 0, 0
    total_pages = max(1, ceil(len(items) / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    return items[start:end], page, total_pages


def _driver_keyboard(
    drivers: list[str], page: int, has_selection: bool
) -> tuple[types.ReplyKeyboardMarkup, int, int]:
    """Строит клавиатуру выбора водителя и возвращает параметры пагинации."""

    page_items, actual_page, total_pages = _paginate(drivers, page)
    builder = ReplyKeyboardBuilder()
    for name in page_items:
        builder.row(types.KeyboardButton(text=name))
    if total_pages > 1:
        nav_buttons: list[types.KeyboardButton] = []
        if actual_page > 0:
            nav_buttons.append(types.KeyboardButton(text=BTN_PAGE_PREV))
        if actual_page < total_pages - 1:
            nav_buttons.append(types.KeyboardButton(text=BTN_PAGE_NEXT))
        if nav_buttons:
            builder.row(*nav_buttons)
    builder.row(types.KeyboardButton(text=BTN_ADD_DRIVER))
    if has_selection:
        builder.row(types.KeyboardButton(text=BTN_NEXT))
    builder.row(
        types.KeyboardButton(text=BTN_BACK),
        types.KeyboardButton(text=BTN_HOME),
    )
    return builder.as_markup(resize_keyboard=True), actual_page, total_pages


def _workers_keyboard(
    workers: list[str], page: int
) -> tuple[types.ReplyKeyboardMarkup, int, int]:
    """Строит клавиатуру выбора рабочих."""

    page_items, actual_page, total_pages = _paginate(workers, page)
    builder = ReplyKeyboardBuilder()
    for name in page_items:
        builder.row(types.KeyboardButton(text=name))
    if total_pages > 1:
        nav_buttons: list[types.KeyboardButton] = []
        if actual_page > 0:
            nav_buttons.append(types.KeyboardButton(text=BTN_PAGE_PREV))
        if actual_page < total_pages - 1:
            nav_buttons.append(types.KeyboardButton(text=BTN_PAGE_NEXT))
        if nav_buttons:
            builder.row(*nav_buttons)
    builder.row(types.KeyboardButton(text=BTN_ADD_WORKER))
    builder.row(types.KeyboardButton(text=BTN_CLEAR_WORKERS))
    builder.row(types.KeyboardButton(text=BTN_CONFIRM))
    builder.row(
        types.KeyboardButton(text=BTN_BACK),
        types.KeyboardButton(text=BTN_HOME),
    )
    return builder.as_markup(resize_keyboard=True), actual_page, total_pages


async def _clear_workers_message(message: types.Message, state: FSMContext) -> None:
    """Удаляет сообщение со списком выбранных рабочих, если оно есть."""

    data = await state.get_data()
    msg_id = data.get("workers_message_id")
    if not msg_id:
        return
    try:
        await message.bot.delete_message(message.chat.id, msg_id)
    except TelegramBadRequest:
        pass
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось удалить сообщение списка рабочих", exc_info=True)
    await state.update_data(workers_message_id=None)


async def _update_selected_workers_view(
    message: types.Message, state: FSMContext
) -> None:
    """Обновляет сообщение со списком выбранных рабочих и inline-кнопками удаления."""

    data = await state.get_data()
    selected: list[str] = data.get("selected_workers", []) or []
    msg_id = data.get("workers_message_id")

    if not selected:
        text = "рабочие пока не выбраны."
        markup = None
    else:
        lines = [
            "выбранные рабочие:",
            *(f"{idx + 1}) {name}" for idx, name in enumerate(selected)),
        ]
        text = "\n".join(lines)
        builder = InlineKeyboardBuilder()
        for idx, name in enumerate(selected):
            builder.button(text=f"✖ {name}", callback_data=f"{REMOVE_PREFIX}{idx}")
        builder.adjust(1)
        markup = builder.as_markup()

    try:
        if msg_id:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=msg_id,
                reply_markup=markup,
            )
        else:
            sent = await message.answer(text, reply_markup=markup)
            await state.update_data(workers_message_id=sent.message_id)
    except TelegramBadRequest:
        if msg_id:
            try:
                await message.bot.delete_message(message.chat.id, msg_id)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Не удалось удалить устаревшее сообщение со списком рабочих",
                    exc_info=True,
                )
        sent = await message.answer(text, reply_markup=markup)
        await state.update_data(workers_message_id=sent.message_id)


async def _return_to_shift_menu(message: types.Message, state: FSMContext) -> None:
    """Очищает состояние и возвращает пользователя в меню смены."""

    data = await state.get_data()
    user_id = data.get("user_id", message.from_user.id)
    row = data.get("row")
    await _clear_workers_message(message, state)
    await state.clear()
    await _render_shift_menu(
        message,
        user_id,
        row,
        state=state,
        delete_trigger_message=False,
        show_progress=False,
    )


async def _go_home(message: types.Message, state: FSMContext) -> None:
    """Очищает состояние и открывает главное меню."""

    from features.main_menu import show_menu

    await _clear_workers_message(message, state)
    await state.clear()
    await show_menu(message, state=state)


async def _refresh_drivers(state: FSMContext) -> list[str]:
    """Читает актуальный список водителей и сохраняет его в состоянии."""

    service = _get_service()
    drivers = await asyncio.to_thread(service.list_active_drivers)
    await state.update_data(drivers=drivers)
    return drivers


async def _refresh_workers(state: FSMContext) -> list[str]:
    """Читает актуальный список рабочих и сохраняет его в состоянии."""

    service = _get_service()
    workers = await asyncio.to_thread(service.list_active_workers)
    await state.update_data(workers=workers)
    return workers


async def _resolve_user_id(
    message: types.Message,
    state: FSMContext,
    provided: int | None = None,
) -> int | None:
    """Определяет фактический user_id для запуска сценария."""

    if provided is not None:
        return provided

    if message.from_user and not message.from_user.is_bot:
        return message.from_user.id

    data = await state.get_data()
    for key in ("user_id", "_shift_user_id"):
        candidate = data.get(key)
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str) and candidate.isdigit():
            return int(candidate)

    if message.chat and message.chat.type == "private":
        return message.chat.id
    return None


@router.message(Command("crew"))
async def start_crew(
    message: types.Message,
    state: FSMContext,
    *,
    user_id: int | None = None,
) -> None:
    """Запускает сценарий заполнения состава бригады."""

    await safe_delete(message)
    actual_user_id = await _resolve_user_id(message, state, user_id)
    if actual_user_id is None:
        await message.answer(
            "Не удалось определить пользователя. Начните смену заново через главное меню."
        )
        await state.clear()
        return
    service = _get_service()

    try:
        row = await asyncio.to_thread(service.get_shift_row_index_for_user, actual_user_id)
        if row is None:
            row = await asyncio.to_thread(service.open_shift_for_user, actual_user_id)
        drivers = await asyncio.to_thread(service.list_active_drivers)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось подготовить данные раздела «Бригада» (user_id=%s)",
            actual_user_id,
        )
        await message.answer(
            "Не удалось открыть раздел «Бригада». Попробуйте позже или обратитесь к координатору."
        )
        await state.clear()
        return

    await state.update_data(
        user_id=actual_user_id,
        row=row,
        drivers=drivers,
        driver_page=0,
        selected_driver=None,
        workers=[],
        worker_page=0,
        selected_workers=[],
        workers_message_id=None,
    )
    await ask_driver(message, state)


async def ask_driver(message: types.Message, state: FSMContext, *, refresh: bool = False) -> None:
    """Отправляет пользователю список водителей для выбора."""

    if refresh:
        drivers = await _refresh_drivers(state)
    else:
        data = await state.get_data()
        drivers = data.get("drivers")
        if drivers is None:
            drivers = await _refresh_drivers(state)
    data = await state.get_data()
    selected_driver = data.get("selected_driver")
    selected_workers = data.get("selected_workers", []) or []
    page = data.get("driver_page", 0)

    keyboard, actual_page, total_pages = _driver_keyboard(
        drivers, page, bool(selected_driver)
    )
    await state.update_data(driver_page=actual_page)

    lines = [
        "выберите водителя из списка или добавьте нового.",
        f"текущий выбор: {selected_driver or 'не выбран'}.",
        f"рабочих выбрано: {len(selected_workers)}.",
    ]
    if total_pages > 1:
        lines.append(f"страница {actual_page + 1} из {total_pages}.")
    if not drivers:
        lines.append("список пуст — добавьте водителя.")

    await state.set_state(CrewStates.choose_driver)
    await message.answer("\n".join(lines), reply_markup=keyboard)


async def ask_workers(
    message: types.Message,
    state: FSMContext,
    *,
    refresh: bool = False,
) -> None:
    """Отправляет список рабочих и отображает выбранных."""

    data = await state.get_data()
    driver = data.get("selected_driver")
    if not driver:
        await message.answer("сначала выберите водителя.")
        await ask_driver(message, state)
        return

    if refresh:
        workers = await _refresh_workers(state)
    else:
        workers = data.get("workers")
        if workers is None:
            workers = await _refresh_workers(state)

    selected_workers: list[str] = data.get("selected_workers", []) or []
    active_keys = {item.casefold() for item in workers}
    filtered_workers = [w for w in selected_workers if w.casefold() in active_keys]
    if filtered_workers != selected_workers:
        selected_workers = filtered_workers
        await state.update_data(selected_workers=selected_workers)

    page = data.get("worker_page", 0)
    keyboard, actual_page, total_pages = _workers_keyboard(workers, page)
    await state.update_data(worker_page=actual_page)

    lines = [
        f"водитель: {driver}",
        f"рабочие выбраны: {len(selected_workers)}.",
        "выберите рабочих из списка или добавьте новых.",
    ]
    if total_pages > 1:
        lines.append(f"страница {actual_page + 1} из {total_pages}.")
    if not workers:
        lines.append("список пуст — добавьте рабочего.")

    await state.set_state(CrewStates.choose_workers)
    await message.answer("\n".join(lines), reply_markup=keyboard)
    await _update_selected_workers_view(message, state)


def _match_choice(candidates: list[str], text: str) -> str | None:
    """Возвращает имя из списка кандидатов, совпавшее без учёта регистра."""

    lowered = text.casefold()
    for name in candidates:
        if name.casefold() == lowered:
            return name
    return None


@router.message(CrewStates.choose_driver)
async def handle_driver_choice(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает выбор водителя и навигацию по списку."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text == BTN_BACK:
        await _return_to_shift_menu(message, state)
        return

    data = await state.get_data()
    drivers: list[str] = data.get("drivers", []) or []
    page = data.get("driver_page", 0)

    if text == BTN_PAGE_PREV:
        await state.update_data(driver_page=max(0, page - 1))
        await ask_driver(message, state)
        return
    if text == BTN_PAGE_NEXT:
        await state.update_data(driver_page=page + 1)
        await ask_driver(message, state)
        return
    if text == BTN_ADD_DRIVER:
        await state.update_data(new_driver={})
        await ask_new_driver_lastname(message, state)
        return
    if text == BTN_NEXT:
        if data.get("selected_driver"):
            await ask_workers(message, state)
        else:
            await message.answer("сначала выберите водителя из списка.")
        return

    if not drivers:
        await message.answer("список пуст — добавьте водителя.")
        return

    choice = _match_choice(drivers, text)
    if not choice:
        await message.answer("используйте кнопки списка или добавьте нового водителя.")
        return

    await state.update_data(selected_driver=choice)
    await message.answer(f"водитель «{choice}» выбран.")
    await ask_workers(message, state)


@router.message(CrewStates.choose_workers)
async def handle_worker_choice(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает выбор рабочих, пагинацию и дополнительные действия."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text == BTN_BACK:
        await state.update_data(worker_page=0)
        await _clear_workers_message(message, state)
        await ask_driver(message, state)
        return

    data = await state.get_data()
    workers: list[str] = data.get("workers", []) or []
    page = data.get("worker_page", 0)
    selected_workers: list[str] = data.get("selected_workers", []) or []

    if text == BTN_PAGE_PREV:
        await state.update_data(worker_page=max(0, page - 1))
        await ask_workers(message, state)
        return
    if text == BTN_PAGE_NEXT:
        await state.update_data(worker_page=page + 1)
        await ask_workers(message, state)
        return
    if text == BTN_ADD_WORKER:
        await state.update_data(new_worker={})
        await ask_new_worker_lastname(message, state)
        return
    if text == BTN_CLEAR_WORKERS:
        await state.update_data(selected_workers=[])
        await _update_selected_workers_view(message, state)
        await message.answer("список рабочих очищен.")
        return
    if text == BTN_CONFIRM:
        if not data.get("selected_driver"):
            await message.answer("сначала выберите водителя.")
            await ask_driver(message, state)
            return
        if not selected_workers:
            await message.answer("нужно выбрать хотя бы одного рабочего.")
            return
        await ask_confirm(message, state)
        return

    if not workers:
        await message.answer("список пуст — добавьте рабочего.")
        return

    choice = _match_choice(workers, text)
    if not choice:
        await message.answer("используйте кнопки списка или добавьте нового рабочего.")
        return

    if any(worker.casefold() == choice.casefold() for worker in selected_workers):
        await message.answer("этот рабочий уже выбран.")
        return

    selected_workers.append(choice)
    await state.update_data(selected_workers=selected_workers)
    await message.answer(f"рабочий «{choice}» добавлен в список.")
    await _update_selected_workers_view(message, state)


@router.callback_query(F.data.startswith(REMOVE_PREFIX))
async def handle_worker_removal(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Удаляет рабочего из выбранного списка по нажатию inline-кнопки."""

    data = await state.get_data()
    selected: list[str] = data.get("selected_workers", []) or []
    payload = callback.data or ""
    index_text = payload[len(REMOVE_PREFIX) :]

    try:
        index = int(index_text)
    except ValueError:
        await callback.answer("не найдено", show_alert=False)
        return

    if index < 0 or index >= len(selected):
        await callback.answer("не найдено", show_alert=False)
        return

    removed = selected.pop(index)
    await state.update_data(selected_workers=selected)

    if callback.message:
        await _update_selected_workers_view(callback.message, state)

    await callback.answer(f"Удалено: {removed}")


async def ask_new_driver_lastname(message: types.Message, state: FSMContext) -> None:
    """Запрашивает фамилию для нового водителя."""

    await state.set_state(CrewStates.add_driver_lastname)
    await message.answer("введите фамилию водителя.", reply_markup=_nav_keyboard())


@router.message(CrewStates.add_driver_lastname)
async def handle_new_driver_lastname(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод фамилии при добавлении водителя."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text == BTN_BACK:
        await ask_driver(message, state)
        return

    try:
        last = validate_name_piece(text)
    except ValueError:
        await message.answer("в этом поле допустимы только буквы, пробел и дефис")
        return

    await state.update_data(new_driver={"last": last})
    await ask_new_driver_firstname(message, state)


async def ask_new_driver_firstname(message: types.Message, state: FSMContext) -> None:
    """Запрашивает имя для нового водителя."""

    await state.set_state(CrewStates.add_driver_firstname)
    await message.answer("введите имя водителя.", reply_markup=_nav_keyboard())


@router.message(CrewStates.add_driver_firstname)
async def handle_new_driver_firstname(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод имени при добавлении водителя."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text == BTN_BACK:
        await ask_new_driver_lastname(message, state)
        return

    try:
        first = validate_name_piece(text)
    except ValueError:
        await message.answer("в этом поле допустимы только буквы, пробел и дефис")
        return

    data = await state.get_data()
    payload = data.get("new_driver", {})
    payload["first"] = first
    await state.update_data(new_driver=payload)
    await ask_new_driver_middlename(message, state)


async def ask_new_driver_middlename(message: types.Message, state: FSMContext) -> None:
    """Запрашивает отчество для нового водителя."""

    await state.set_state(CrewStates.add_driver_middlename)
    await message.answer(
        "введите отчество водителя (можно «Пропустить»).",
        reply_markup=_nav_keyboard([BTN_SKIP]),
    )


@router.message(CrewStates.add_driver_middlename)
async def handle_new_driver_middlename(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод отчества при добавлении водителя."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text == BTN_BACK:
        await ask_new_driver_firstname(message, state)
        return

    data = await state.get_data()
    payload = data.get("new_driver", {})
    last = (payload or {}).get("last")
    first = (payload or {}).get("first")

    if not last or not first:
        await message.answer("данные водителя потеряны. начните заново.")
        await ask_driver(message, state, refresh=True)
        return

    if text == BTN_SKIP:
        middle = ""
    else:
        try:
            middle = validate_name_piece(text)
        except ValueError:
            await message.answer("в этом поле допустимы только буквы, пробел и дефис")
            return

    await state.update_data(new_driver=None)
    await _finalize_new_driver(message, state, last, first, middle)


async def _finalize_new_driver(
    message: types.Message,
    state: FSMContext,
    last: str,
    first: str,
    middle: str,
) -> None:
    """Добавляет водителя в справочник и переходит к выбору рабочих."""

    short_name = format_compact_fio(last, first, middle)
    service = _get_service()

    try:
        status = await asyncio.to_thread(service.get_driver_status, short_name)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось проверить наличие водителя %s", short_name)
        await message.answer(
            "Не удалось проверить справочник водителей. Попробуйте позже или обратитесь к координатору."
        )
        await ask_driver(message, state, refresh=True)
        return

    if status is not None:
        msg = "эта запись находится в архиве." if status.strip().lower() == "архив" else "такое ФИО уже есть в списке."
        await message.answer(msg)
        await ask_driver(message, state, refresh=True)
        return

    try:
        await asyncio.to_thread(service.add_driver, short_name)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось добавить водителя %s", short_name)
        await message.answer(
            "Не удалось добавить водителя. Попробуйте позже или обратитесь к координатору."
        )
        await ask_driver(message, state, refresh=True)
        return

    await _refresh_drivers(state)
    await state.update_data(selected_driver=short_name, driver_page=0)
    await message.answer(f"водитель «{short_name}» добавлен и выбран.")
    await ask_workers(message, state, refresh=True)


async def ask_new_worker_lastname(message: types.Message, state: FSMContext) -> None:
    """Запрашивает фамилию нового рабочего."""

    await state.set_state(CrewStates.add_worker_lastname)
    await message.answer("введите фамилию рабочего.", reply_markup=_nav_keyboard())


@router.message(CrewStates.add_worker_lastname)
async def handle_new_worker_lastname(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод фамилии при добавлении рабочего."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text == BTN_BACK:
        await ask_workers(message, state)
        return

    try:
        last = validate_name_piece(text)
    except ValueError:
        await message.answer("в этом поле допустимы только буквы, пробел и дефис")
        return

    await state.update_data(new_worker={"last": last})
    await ask_new_worker_firstname(message, state)


async def ask_new_worker_firstname(message: types.Message, state: FSMContext) -> None:
    """Запрашивает имя нового рабочего."""

    await state.set_state(CrewStates.add_worker_firstname)
    await message.answer("введите имя рабочего.", reply_markup=_nav_keyboard())


@router.message(CrewStates.add_worker_firstname)
async def handle_new_worker_firstname(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод имени при добавлении рабочего."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text == BTN_BACK:
        await ask_new_worker_lastname(message, state)
        return

    try:
        first = validate_name_piece(text)
    except ValueError:
        await message.answer("в этом поле допустимы только буквы, пробел и дефис")
        return

    data = await state.get_data()
    payload = data.get("new_worker", {})
    payload["first"] = first
    await state.update_data(new_worker=payload)
    await ask_new_worker_middlename(message, state)


async def ask_new_worker_middlename(message: types.Message, state: FSMContext) -> None:
    """Запрашивает отчество нового рабочего."""

    await state.set_state(CrewStates.add_worker_middlename)
    await message.answer(
        "введите отчество рабочего (можно «Пропустить»).",
        reply_markup=_nav_keyboard([BTN_SKIP]),
    )


@router.message(CrewStates.add_worker_middlename)
async def handle_new_worker_middlename(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод отчества при добавлении рабочего."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text == BTN_BACK:
        await ask_new_worker_firstname(message, state)
        return

    data = await state.get_data()
    payload = data.get("new_worker", {})
    last = (payload or {}).get("last")
    first = (payload or {}).get("first")

    if not last or not first:
        await message.answer("данные рабочего потеряны. начните заново.")
        await ask_workers(message, state, refresh=True)
        return

    if text == BTN_SKIP:
        middle = ""
    else:
        try:
            middle = validate_name_piece(text)
        except ValueError:
            await message.answer("в этом поле допустимы только буквы, пробел и дефис")
            return

    await state.update_data(new_worker=None)
    await _finalize_new_worker(message, state, last, first, middle)


async def _finalize_new_worker(
    message: types.Message,
    state: FSMContext,
    last: str,
    first: str,
    middle: str,
) -> None:
    """Добавляет рабочего в справочник и отмечает его выбранным."""

    short_name = format_compact_fio(last, first, middle)
    service = _get_service()

    try:
        status = await asyncio.to_thread(service.get_worker_status, short_name)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось проверить наличие рабочего %s", short_name)
        await message.answer(
            "Не удалось проверить справочник рабочих. Попробуйте позже или обратитесь к координатору."
        )
        await ask_workers(message, state, refresh=True)
        return

    if status is not None:
        msg = "эта запись находится в архиве." if status.strip().lower() == "архив" else "такое ФИО уже есть в списке."
        await message.answer(msg)
        await ask_workers(message, state, refresh=True)
        return

    try:
        await asyncio.to_thread(service.add_worker, short_name)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось добавить рабочего %s", short_name)
        await message.answer(
            "Не удалось добавить рабочего. Попробуйте позже или обратитесь к координатору."
        )
        await ask_workers(message, state, refresh=True)
        return

    await _refresh_workers(state)
    data = await state.get_data()
    selected_workers: list[str] = data.get("selected_workers", []) or []
    if not any(worker.casefold() == short_name.casefold() for worker in selected_workers):
        selected_workers.append(short_name)
    await state.update_data(selected_workers=selected_workers, worker_page=0)
    await message.answer(f"рабочий «{short_name}» добавлен и выбран.")
    await ask_workers(message, state, refresh=True)


async def ask_confirm(message: types.Message, state: FSMContext) -> None:
    """Показывает итоговый состав бригады перед сохранением."""

    data = await state.get_data()
    driver = data.get("selected_driver")
    workers: list[str] = data.get("selected_workers", []) or []

    if not driver or not workers:
        await message.answer("сначала выберите водителя и рабочих.")
        await ask_workers(message, state)
        return

    lines = [
        "проверьте состав бригады:",
        f"водитель: {driver}",
        "рабочие:",
        *(f"- {name}" for name in workers),
        "сохранить?",
    ]

    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text=BTN_CONFIRM))
    builder.row(types.KeyboardButton(text=BTN_EDIT))
    builder.row(types.KeyboardButton(text=BTN_BACK), types.KeyboardButton(text=BTN_HOME))

    await state.set_state(CrewStates.confirm)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup(resize_keyboard=True))


@router.message(CrewStates.confirm)
async def handle_confirm(message: types.Message, state: FSMContext) -> None:
    """Сохраняет состав бригады или возвращает к редактированию."""

    await safe_delete(message)
    text = (message.text or "").strip()

    if text == BTN_HOME:
        await _go_home(message, state)
        return
    if text in {BTN_BACK, BTN_EDIT}:
        await ask_workers(message, state)
        return

    if text != BTN_CONFIRM:
        await message.answer("используйте кнопки для подтверждения или возврата к редактированию.")
        return

    data = await state.get_data()
    driver = data.get("selected_driver")
    workers: list[str] = data.get("selected_workers", []) or []
    row = data.get("row")
    user_id = data.get("user_id", message.from_user.id)

    if not driver or not workers or not row:
        await message.answer("недостаточно данных для сохранения. заполните раздел ещё раз.")
        await ask_workers(message, state)
        return

    service = _get_service()
    try:
        await asyncio.to_thread(
            service.save_crew,
            row,
            driver=driver,
            workers=workers,
            telegram_id=user_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сохранить состав бригады (user_id=%s, row=%s)", user_id, row)
        await message.answer(
            "Не удалось сохранить состав бригады. Попробуйте позже или обратитесь к координатору."
        )
        return

    await _clear_workers_message(message, state)
    await state.clear()
    from features.shift_menu import mark_mode_done

    mark_mode_done(user_id, "crew")
    await message.answer("состав бригады сохранён. возвращаю в меню смены…")
    await _render_shift_menu(
        message,
        user_id,
        row,
        state=state,
        delete_trigger_message=False,
        show_progress=False,
    )
