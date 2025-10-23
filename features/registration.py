from __future__ import annotations

"""FSM регистрации пользователей.

Логика реализует пошаговое заполнение ФИО и запись данных в лист «Данные».
"""

import os

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from services.sheets import SheetsService, validate_name_piece

router = Router()
service = SheetsService()

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]


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

    try:
        row, status = service.find_row_by_telegram_id(SPREADSHEET_ID, user_id)
    except Exception:
        row, status = None, None

    if row and status != "Архив":
        await message.answer("Добро пожаловать! Вы уже зарегистрированы. Открываю панель.")
        return

    if row and status == "Архив":
        await message.answer("Ваш доступ отключён. Обратитесь к координатору (статус: Архив).")
        return

    await state.clear()
    await state.set_state(RegStates.last)
    await message.answer("Введите вашу Фамилию (только буквы).")


@router.message(RegStates.last)
async def reg_last(message: types.Message, state: FSMContext) -> None:
    ok, value = validate_name_piece(message.text or "")
    if not ok:
        await message.answer(f"Некорректная фамилия: {value}")
        return

    await state.update_data(last=value)
    await state.set_state(RegStates.first)
    await message.answer("Введите ваше Имя (только буквы).")


@router.message(RegStates.first)
async def reg_first(message: types.Message, state: FSMContext) -> None:
    ok, value = validate_name_piece(message.text or "")
    if not ok:
        await message.answer(f"Некорректное имя: {value}")
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
    ok, value = validate_name_piece(message.text or "")
    if not ok:
        await message.answer(f"Некорректное отчество: {value}")
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

    if service.fio_duplicate_exists(SPREADSHEET_ID, last, first, middle):
        await message.answer(
            "Пользователь с таким ФИО уже зарегистрирован. Уточните данные или обратитесь к координатору."
        )
        return

    try:
        service.upsert_registration_row(
            spreadsheet_id=SPREADSHEET_ID,
            telegram_id=user_id,
            last=last,
            first=first,
            middle=middle,
        )
    except PermissionError:
        await message.answer("Ваш доступ отключён. Обратитесь к координатору.")
        return
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"Ошибка сохранения: {exc}")
        return

    await state.clear()
    await message.answer(
        "Регистрация завершена. Статус: Активен. Открываю панель.",
        reply_markup=types.ReplyKeyboardRemove(),
    )

    # TODO: подключить показ меню/дашборда после регистрации.
