"""FSM регистрации пользователей."""

from __future__ import annotations

import asyncio

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from services.env import require_env
from services.sheets import SheetsService, validate_name_piece
from features.main_menu import show_menu

router = Router()

_service: SheetsService | None = None


def _get_service() -> SheetsService:
    """Ленивая инициализация сервиса работы с Google Sheets."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


def _get_spreadsheet_id() -> str:
    """Возвращает идентификатор таблицы из окружения."""

    return require_env("SPREADSHEET_ID")


class RegStates(StatesGroup):
    """Состояния FSM регистрации."""

    last = State()
    first = State()
    middle = State()
    confirm = State()


def _btns_confirm() -> types.ReplyKeyboardMarkup:
    keyboard = [
        [types.KeyboardButton(text="Подтвердить")],
        [types.KeyboardButton(text="Ввести заново")],
    ]
    return types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def _btns_skip_back() -> types.ReplyKeyboardMarkup:
    keyboard = [
        [types.KeyboardButton(text="Пропустить")],
        [types.KeyboardButton(text="Отмена")],
    ]
    return types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    """Стартовая точка: проверяет регистрацию и запускает FSM при необходимости."""

    user_id = message.from_user.id

    service = _get_service()
    spreadsheet_id = _get_spreadsheet_id()

    try:
        row, status = await asyncio.to_thread(
            service.find_row_by_telegram_id, spreadsheet_id, user_id
        )
    except PermissionError:
        await message.answer(
            "Ваш доступ заблокирован. Обратитесь к координатору для уточнения статуса."
        )
        return
    except Exception:  # noqa: BLE001
        await message.answer(
            "Не удалось проверить регистрацию. Попробуйте повторить попытку позже."
        )
        return

    if row and status != "Архив":
        await message.answer("Добро пожаловать! Вы уже зарегистрированы. Открываю панель.")
        await show_menu(message, service=service, state=state)
        return

    if row and status == "Архив":
        await message.answer("Ваш доступ отключён. Обратитесь к координатору (статус: Архив).")
        return

    await state.clear()
    await state.set_state(RegStates.last)
    await message.answer("Введите вашу Фамилию (только буквы).", reply_markup=types.ReplyKeyboardRemove())


@router.message(RegStates.last)
async def reg_last(message: types.Message, state: FSMContext) -> None:
    try:
        value = validate_name_piece(message.text or "")
    except ValueError as error:
        await message.answer(f"Некорректная фамилия: {error}")
        return

    await state.update_data(last=value)
    await state.set_state(RegStates.first)
    await message.answer("Введите ваше Имя (только буквы).")


@router.message(RegStates.first)
async def reg_first(message: types.Message, state: FSMContext) -> None:
    try:
        value = validate_name_piece(message.text or "")
    except ValueError as error:
        await message.answer(f"Некорректное имя: {error}")
        return

    await state.update_data(first=value)
    await state.set_state(RegStates.middle)
    await message.answer(
        "Введите ваше Отчество (если нет — нажмите «Пропустить»).",
        reply_markup=_btns_skip_back(),
    )


@router.message(RegStates.middle, F.text.casefold() == "пропустить")
async def reg_middle_skip(message: types.Message, state: FSMContext) -> None:
    await state.update_data(middle="")
    await _to_confirm(message, state)


@router.message(RegStates.middle)
async def reg_middle(message: types.Message, state: FSMContext) -> None:
    try:
        value = validate_name_piece(message.text or "")
    except ValueError as error:
        await message.answer(f"Некорректное отчество: {error}")
        return

    await state.update_data(middle=value)
    await _to_confirm(message, state)


async def _to_confirm(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    fio = " ".join(
        part for part in [data.get("last", ""), data.get("first", ""), data.get("middle", "")] if part
    )
    await state.set_state(RegStates.confirm)
    await message.answer(
        f"Проверьте данные:\n<b>{fio}</b>\n\nНажмите «Подтвердить» или «Ввести заново».",
        reply_markup=_btns_confirm(),
        parse_mode="HTML",
    )


@router.message(RegStates.confirm, F.text.casefold() == "ввести заново")
async def reg_restart(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(RegStates.last)
    await message.answer("Введите вашу Фамилию (только буквы).", reply_markup=types.ReplyKeyboardRemove())


@router.message(RegStates.confirm, F.text.casefold() == "подтвердить")
async def reg_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    last = data["last"]
    first = data["first"]
    middle = data.get("middle", "")
    user_id = message.from_user.id

    service = _get_service()
    spreadsheet_id = _get_spreadsheet_id()

    if await asyncio.to_thread(
        service.fio_duplicate_exists, spreadsheet_id, last, first, middle
    ):
        await message.answer(
            "Пользователь с таким ФИО уже зарегистрирован. Уточните данные или обратитесь к координатору."
        )
        return

    try:
        await asyncio.to_thread(
            service.upsert_registration_row,
            spreadsheet_id,
            user_id,
            last,
            first,
            middle,
        )
    except PermissionError:
        await message.answer("Ваш доступ отключён. Обратитесь к координатору.")
        return
    except Exception:  # noqa: BLE001
        await message.answer("Временная ошибка при сохранении. Попробуйте позже.")
        return

    await state.clear()
    await message.answer(
        "Регистрация завершена. Статус: Активен. Открываю панель.",
        reply_markup=types.ReplyKeyboardRemove(),
    )

    await show_menu(message, service=service, state=state)
