"""Главный сценарий бота управления сменами MSC.

Сценарий построен на FSM aiogram и покрывает регистрацию, дашборд, запуск и
закрытие смены. Все обращения к Google Sheets инкапсулированы в
``SheetsService``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from src.sheets_service import SheetsService
from dotenv import load_dotenv


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(
            f"Не установлена обязательная переменная окружения '{var_name}'."
        )
    return value


load_dotenv()

BOT_TOKEN = _require_env("BOT_TOKEN")
SPREADSHEET_ID = _require_env("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON_PATH = _require_env("SERVICE_ACCOUNT_JSON_PATH")


logging.basicConfig(level=logging.INFO)


class UserFlow(StatesGroup):
    """Основной конечный автомат пользователя."""

    registration_last_name = State()
    registration_first_name = State()
    registration_middle_name = State()
    registration_confirm = State()
    dashboard = State()
    shift_menu = State()
    crew_select_driver = State()
    crew_select_workers = State()
    crew_confirm = State()
    materials_pvd_in = State()
    materials_pvc_in = State()
    materials_tape_in = State()
    materials_pvd_out = State()
    materials_pvc_out = State()
    materials_tape_out = State()
    materials_photos = State()
    materials_review = State()
    expenses_ship = State()
    expenses_holds = State()
    expenses_transport = State()
    expenses_foreman = State()
    expenses_workers = State()
    expenses_aux = State()
    expenses_food = State()
    expenses_taxi = State()
    expenses_other = State()
    expenses_review = State()
    closing_confirmation = State()


@dataclass
class ActiveShift:
    """Структура данных активной смены."""

    rows: Dict[str, int]
    sections: Dict[str, bool]


router = Router()
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)
dp.include_router(router)

sheets_service = SheetsService(
    spreadsheet_id=SPREADSHEET_ID, service_account_path=SERVICE_ACCOUNT_JSON_PATH
)


def skip_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def registration_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Начать регистрацию", callback_data="registration:start")]]
    )


def shift_menu_keyboard(sections: Dict[str, bool]) -> InlineKeyboardMarkup:
    def mark(done: bool) -> str:
        return "✅" if done else "⏳"

    buttons = [
        [
            InlineKeyboardButton(
                text=f"👥 Состав бригады {mark(sections.get('crew', False))}",
                callback_data="shift_section:crew",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"📦 Материалы {mark(sections.get('materials', False))}",
                callback_data="shift_section:materials",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"💸 Расходы {mark(sections.get('expenses', False))}",
                callback_data="shift_section:expenses",
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Закрыть смену",
                callback_data="shift_section:close",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
    "ship": UserFlow.expenses_ship,
    "holds": UserFlow.expenses_holds,
    "transport": UserFlow.expenses_transport,
    "foreman": UserFlow.expenses_foreman,
    "workers": UserFlow.expenses_workers,
    "aux": UserFlow.expenses_aux,
    "food": UserFlow.expenses_food,
    "taxi": UserFlow.expenses_taxi,
    "other": UserFlow.expenses_other,
}


MATERIALS_ORDER = [
    "pvd_in",
    "pvc_in",
    "tape_in",
    "pvd_out",
    "pvc_out",
    "tape_out",
    "photos",
]

MATERIALS_LABELS = {
    "pvd_in": "Рулоны ПВД — поступление",
    "pvc_in": "Трубки ПВХ — поступление",
    "tape_in": "Клейкая лента — поступление",
    "pvd_out": "Рулоны ПВД — расход",
    "pvc_out": "Трубки ПВХ — расход",
    "tape_out": "Клейкая лента — расход",
    "photos": "Фото крепления",
}

MATERIALS_MESSAGES = {
    "pvd_in": "Укажите количество поступивших рулонов ПВД (в метрах).",
    "pvc_in": "Укажите количество поступивших трубок ПВХ (в штуках).",
    "tape_in": "Укажите количество поступившей клейкой ленты (в штуках).",
    "pvd_out": "Укажите расход рулонов ПВД (в метрах).",
    "pvc_out": "Укажите расход трубок ПВХ (в штуках).",
    "tape_out": "Укажите расход клейкой ленты (в штуках).",
}

MATERIALS_STATE_BY_KEY = {
    "pvd_in": UserFlow.materials_pvd_in,
    "pvc_in": UserFlow.materials_pvc_in,
    "tape_in": UserFlow.materials_tape_in,
    "pvd_out": UserFlow.materials_pvd_out,
    "pvc_out": UserFlow.materials_pvc_out,
    "tape_out": UserFlow.materials_tape_out,
    "photos": UserFlow.materials_photos,
}


def dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать оформление смены", callback_data="dashboard:start")]
        ]
    )


def crew_driver_keyboard(drivers: List[str]) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=name)] for name in drivers[:20]]
    keyboard.append([KeyboardButton(text="Отмена")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def crew_workers_keyboard(workers: List[str]) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=name)] for name in workers[:20]]
    keyboard.append([KeyboardButton(text="Готово")])
    keyboard.append([KeyboardButton(text="Отмена")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def build_keyboard(
    rows: Optional[List[List[str]]] = None,
    *,
    include_skip: bool = False,
) -> ReplyKeyboardMarkup:
    keyboard: List[List[KeyboardButton]] = []
    if rows:
        for row in rows:
            keyboard.append([KeyboardButton(text=value) for value in row])
    keyboard.append([KeyboardButton(text="⬅ Назад"), KeyboardButton(text="В меню")])
    if include_skip:
        keyboard.append([KeyboardButton(text="Пропустить")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def format_amount(value: str) -> str:
    cleaned = value.replace(" ", "") if value else ""
    if cleaned.isdigit():
        return f"{int(cleaned):,}".replace(",", " ")
    return value


def materials_photo_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Подтвердить")],
            [KeyboardButton(text="Изменить")],
            [KeyboardButton(text="⬅ Назад"), KeyboardButton(text="В меню")],
        ],
        resize_keyboard=True,
    )


async def ensure_materials_context(state: FSMContext, materials_row: int) -> Dict[str, str]:
    data = await state.get_data()
    materials_data: Optional[Dict[str, str]] = data.get("materials_data")
    if materials_data is None:
        materials_data = await sheets_service.get_materials_details(materials_row)
        await state.update_data(materials_data=materials_data)
    if data.get("materials_photos") is None:
        await state.update_data(materials_photos=[])
    return materials_data


async def prompt_materials_numeric(message: Message, state: FSMContext, key: str) -> None:
    data = await state.get_data()
    materials = data.get("materials_data", {})
    current = materials.get(key, "")
    suffix = f"\nТекущие данные: {current}" if current else ""
    await state.set_state(MATERIALS_STATE_BY_KEY[key])
    await message.answer(
        MATERIALS_MESSAGES[key] + suffix,
        reply_markup=build_keyboard(include_skip=True),
    )


async def prompt_materials_photos(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    materials = data.get("materials_data", {})
    link = materials.get("photo_link", "")
    pending = data.get("materials_photos") or []
    note_parts = []
    if link:
        note_parts.append(f"Текущая ссылка: {link}")
    if pending:
        note_parts.append(f"Получено новых файлов: {len(pending)}")
    note = f"\n{' '.join(note_parts)}" if note_parts else ""
    await state.set_state(UserFlow.materials_photos)
    await message.answer(
        "Прикрепите фото крепления. Можно загрузить несколько файлов подряд. После завершения нажмите 'Подтвердить'."
        + note,
        reply_markup=materials_photo_keyboard(),
    )


async def prompt_materials_step(message: Message, state: FSMContext, key: str) -> None:
    if key == "photos":
        await prompt_materials_photos(message, state)
    else:
        await prompt_materials_numeric(message, state, key)


async def go_to_previous_materials_step(message: Message, state: FSMContext, current_key: str) -> None:
    index = MATERIALS_ORDER.index(current_key)
    if index == 0:
        await go_to_shift_menu(message, state)
        return
    previous_key = MATERIALS_ORDER[index - 1]
    await prompt_materials_step(message, state, previous_key)


async def go_to_next_materials_step(message: Message, state: FSMContext, current_key: str) -> None:
    index = MATERIALS_ORDER.index(current_key)
    if index == len(MATERIALS_ORDER) - 1:
        await show_materials_review(message, state)
        return
    next_key = MATERIALS_ORDER[index + 1]
    await prompt_materials_step(message, state, next_key)


async def show_materials_review(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    materials: Dict[str, str] = data.get("materials_data", {})
    lines = []
    for key in MATERIALS_ORDER[:-1]:
        value = materials.get(key, "") or "0"
        lines.append(f"{MATERIALS_LABELS[key]}: {value}")
    link = materials.get("photo_link") or "—"
    lines.append(f"{MATERIALS_LABELS['photos']}: {link}")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data="materials:confirm")],
            [InlineKeyboardButton(text="Изменить", callback_data="materials:edit")],
        ]
    )
    await state.set_state(UserFlow.materials_review)
    await message.answer(
        "Проверьте введённые данные по материалам. Всё верно?\n\n" + "\n".join(lines),
        reply_markup=keyboard,
    )


async def handle_materials_numeric_input(message: Message, state: FSMContext, key: str) -> None:
    text = (message.text or "").strip()
    lower = text.lower()
    if lower == "в меню":
        await go_to_shift_menu(message, state)
        return
    if lower == "⬅ назад":
        await go_to_previous_materials_step(message, state, key)
        return
    if lower == "пропустить":
        value = "0"
    else:
        cleaned = text.replace(" ", "")
        if not cleaned.isdigit():
            await message.answer("Пожалуйста, укажите целое число или нажмите 'Пропустить'.")
            return
        value = str(int(cleaned))

    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await message.answer("Активная смена не найдена.")
        return
    materials_row = active_shift.rows.get("materials")
    if not materials_row:
        await message.answer("Строка материалов не найдена.")
        return
    materials = await ensure_materials_context(state, materials_row)
    materials[key] = value
    await state.update_data(materials_data=materials)
    await go_to_next_materials_step(message, state, key)


@router.message(UserFlow.materials_pvd_in)
async def materials_pvd_in_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "pvd_in")


@router.message(UserFlow.materials_pvc_in)
async def materials_pvc_in_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "pvc_in")


@router.message(UserFlow.materials_tape_in)
async def materials_tape_in_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "tape_in")


@router.message(UserFlow.materials_pvd_out)
async def materials_pvd_out_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "pvd_out")


@router.message(UserFlow.materials_pvc_out)
async def materials_pvc_out_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "pvc_out")


@router.message(UserFlow.materials_tape_out)
async def materials_tape_out_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "tape_out")


@router.message(UserFlow.materials_photos)
async def materials_photos_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = (message.text or "").strip()
    lower = text.lower()
    if message.photo:
        file_id = message.photo[-1].file_id
        pending: List[str] = data.get("materials_photos") or []
        pending.append(file_id)
        await state.update_data(materials_photos=pending)
        await message.answer(
            f"Фото получено. Всего новых файлов: {len(pending)}.",
            reply_markup=materials_photo_keyboard(),
        )
        return
    if lower == "в меню":
        await go_to_shift_menu(message, state)
        return
    if lower == "⬅ назад":
        await go_to_previous_materials_step(message, state, "photos")
        return
    if lower == "изменить":
        materials = data.get("materials_data") or {}
        materials["photo_link"] = ""
        await state.update_data(materials_data=materials, materials_photos=[])
        await message.answer(
            "Старые файлы очищены. Отправьте новые фотографии.",
            reply_markup=materials_photo_keyboard(),
        )
        return
    if lower == "подтвердить":
        active_shift: Optional[ActiveShift] = data.get("active_shift")
        if not active_shift:
            await message.answer("Активная смена не найдена.")
            return
        materials_row = active_shift.rows.get("materials")
        if not materials_row:
            await message.answer("Строка материалов не найдена.")
            return
        materials = await ensure_materials_context(state, materials_row)
        pending: List[str] = data.get("materials_photos") or []
        link = materials.get("photo_link", "")
        if pending:
            link = await sheets_service.register_materials_photos(materials_row, pending)
            await state.update_data(materials_photos=[])
        materials["photo_link"] = link
        await state.update_data(materials_data=materials)
        await message.answer(
            "Фото обработаны.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await go_to_next_materials_step(message, state, "photos")
        return
    await message.answer(
        "Отправьте фотографии или используйте кнопки управления.",
        reply_markup=materials_photo_keyboard(),
    )


@router.callback_query(F.data == "materials:confirm", UserFlow.materials_review)
async def materials_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена не найдена", show_alert=True)
        return
    materials_row = active_shift.rows.get("materials")
    if not materials_row:
        await callback.answer("Строка материалов не найдена", show_alert=True)
        return
    materials: Dict[str, str] = data.get("materials_data") or {}
    pending: List[str] = data.get("materials_photos") or []
    link = materials.get("photo_link", "")
    if pending:
        link = await sheets_service.register_materials_photos(materials_row, pending)
        await state.update_data(materials_photos=[])
        materials["photo_link"] = link
    normalized: Dict[str, str] = {}
    for key in MATERIALS_ORDER[:-1]:
        raw = str(materials.get(key, "") or "0")
        cleaned = raw.replace(" ", "")
        normalized[key] = str(int(cleaned)) if cleaned.isdigit() else "0"
    await sheets_service.save_materials_numbers(materials_row, normalized)
    await sheets_service.save_materials_photo_link(materials_row, link)
    materials.update(normalized)
    materials["photo_link"] = link
    await state.update_data(materials_data=materials, materials_photos=[])
    active_shift.sections["materials"] = True
    await state.update_data(active_shift=active_shift)
    await state.set_state(UserFlow.shift_menu)
    await callback.message.answer(
        "Данные по материалам сохранены.",
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )
    await callback.answer("Раздел заполнен")


@router.callback_query(F.data == "materials:edit", UserFlow.materials_review)
async def materials_edit(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена не найдена", show_alert=True)
        return
    materials_row = active_shift.rows.get("materials")
    if not materials_row:
        await callback.answer("Строка материалов не найдена", show_alert=True)
        return
    await ensure_materials_context(state, materials_row)
    await state.update_data(materials_photos=[])
    await prompt_materials_step(callback.message, state, MATERIALS_ORDER[0])
    await callback.answer()


async def go_to_shift_menu(message: Message, state: FSMContext, notice: str = "Возвращаю в меню смены.") -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await state.set_state(UserFlow.dashboard)
        await message.answer(
            "Активная смена не найдена. Возвращаю в личный кабинет.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await send_dashboard(message, state)
        return
    await state.set_state(UserFlow.shift_menu)
    await message.answer(
        notice,
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )


async def send_dashboard(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_row = data.get("user_row")
    if not user_row:
        user_row = await sheets_service.ensure_user_row(message.from_user.id)
        await state.update_data(user_row=user_row)

    info = await sheets_service.get_dashboard_info(user_row)
    text = (
        f"Здравствуйте, {info['display_name']}!\n\n"
        "Это ваш личный кабинет MSCBaltic.\n"
        f"Вы закрыли {info['closed_count']} смен."
    )
    if info["last_closed"]:
        text += f"\nПоследняя закрытая смена: {info['last_closed']}"
    await state.set_state(UserFlow.dashboard)
    await message.answer(text, reply_markup=dashboard_keyboard())


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
    await state.set_state(UserFlow.expenses_ship)
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
    await state.set_state(UserFlow.expenses_holds)
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
    await state.set_state(UserFlow.expenses_review)
    await message.answer(review_text, reply_markup=keyboard)
@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_row = await sheets_service.find_user_row(message.from_user.id)
    if user_row is not None:
        await state.update_data(user_row=user_row)
        await send_dashboard(message, state)
        return

    await message.answer(
        "Здравствуйте! Я — официальный помощник MSCBaltic. Помогаю быстро и точно оформлять смены."
        "\nДля начала — зарегистрируйтесь.",
        reply_markup=registration_start_keyboard(),
    )


@router.callback_query(F.data == "registration:start")
async def registration_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserFlow.registration_last_name)
    await state.update_data(last_name="", first_name="", middle_name="")
    await callback.message.answer(
        "Введите вашу фамилию.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer()


@router.message(UserFlow.registration_last_name)
async def registration_last_name(message: Message, state: FSMContext) -> None:
    last_name = message.text.strip()
    if not last_name:
        await message.answer("Пожалуйста, введите фамилию.")
        return
    await state.update_data(last_name=last_name)
    await state.set_state(UserFlow.registration_first_name)
    await message.answer(
        "Введите ваше имя.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(UserFlow.registration_first_name)
async def registration_first_name(message: Message, state: FSMContext) -> None:
    first_name = message.text.strip()
    if not first_name:
        await message.answer("Имя не может быть пустым. Введите имя.")
        return
    await state.update_data(first_name=first_name)
    await state.set_state(UserFlow.registration_middle_name)
    await message.answer(
        "Введите ваше отчество или нажмите «Пропустить», если его нет.",
        reply_markup=skip_keyboard(),
    )


@router.message(UserFlow.registration_middle_name)
async def registration_middle_name(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    middle_name = "" if text.lower() == "пропустить" else text
    await state.update_data(middle_name=middle_name)

    data = await state.get_data()
    fio = " ".join(filter(None, [data.get("last_name"), data.get("first_name"), data.get("middle_name")]))
    if not fio:
        fio = message.from_user.full_name or "Без ФИО"

    text = (
        "Ваше ФИО: "
        f"{fio}.\n"
        "Всё верно?"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data="registration:confirm")],
            [InlineKeyboardButton(text="Заполнить заново", callback_data="registration:restart")],
        ]
    )
    await state.set_state(UserFlow.registration_confirm)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "registration:restart", UserFlow.registration_confirm)
async def registration_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserFlow.registration_last_name)
    await state.update_data(last_name="", first_name="", middle_name="")
    await callback.message.answer(
        "Хорошо, давайте начнём заново. Введите вашу фамилию.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer()


@router.callback_query(F.data == "registration:confirm", UserFlow.registration_confirm)
async def registration_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user_row = await sheets_service.ensure_user_row(callback.from_user.id)
    await sheets_service.update_user_fio(
        user_row,
        data.get("last_name", ""),
        data.get("first_name", ""),
        data.get("middle_name", ""),
    )
    await state.update_data(user_row=user_row)
    await callback.answer("Регистрация завершена")
    await send_dashboard(callback.message, state)


@router.callback_query(F.data == "dashboard:start", UserFlow.dashboard)
async def start_shift(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user_row = data.get("user_row")
    if not user_row:
        user_row = await sheets_service.ensure_user_row(callback.from_user.id)
        await state.update_data(user_row=user_row)

    fio = await sheets_service.get_user_fio(user_row)
    if not fio:
        fio = callback.from_user.full_name or "Неизвестный пользователь"

    shift_rows = await sheets_service.append_shift_rows(callback.from_user.id, fio)
    active_shift = ActiveShift(
        rows={
            "expenses": shift_rows.expenses_row,
            "materials": shift_rows.materials_row,
            "crew": shift_rows.crew_row,
        },
        sections={"crew": False, "materials": False, "expenses": False},
    )
    await state.update_data(
        active_shift=active_shift,
        expenses_data=None,
        ships_directory=None,
        materials_data=None,
        materials_photos=None,
    )
    await state.set_state(UserFlow.shift_menu)
    await callback.message.answer(
        "Смена создана. Используйте меню ниже для заполнения разделов.",
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shift_section:"), UserFlow.shift_menu)
async def handle_shift_section(callback: CallbackQuery, state: FSMContext) -> None:
    section = callback.data.split(":", maxsplit=1)[1]
    data = await state.get_data()
    active_shift: ActiveShift = data.get("active_shift")
    if not active_shift:
        await callback.answer("Смена не найдена", show_alert=True)
        return

    if section == "crew":
        drivers = await sheets_service.get_drivers_directory()
        if not drivers:
            await callback.answer("Нет данных о водителях", show_alert=True)
            return
        await state.set_state(UserFlow.crew_select_driver)
        await callback.message.answer(
            "Выберите водителя:", reply_markup=crew_driver_keyboard(drivers)
        )
    elif section == "materials":
        materials_row = active_shift.rows.get("materials")
        if not materials_row:
            await callback.answer("Строка материалов не найдена", show_alert=True)
            return
        await ensure_materials_context(state, materials_row)
        await prompt_materials_step(callback.message, state, MATERIALS_ORDER[0])
    elif section == "expenses":
        expenses_row = active_shift.rows.get("expenses")
        if not expenses_row:
            await callback.answer("Строка расходов не найдена", show_alert=True)
            return
        await ensure_expenses_context(state, expenses_row)
        await prompt_expenses_ship(callback.message, state)
    elif section == "close":
        if all(active_shift.sections.values()):
            await state.set_state(UserFlow.closing_confirmation)
            await callback.message.answer(
                "Все разделы заполнены. Подтвердите закрытие смены.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Закрыть смену",
                                callback_data="shift_close:confirm",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="Отмена",
                                callback_data="shift_close:cancel",
                            )
                        ],
                    ]
                ),
            )
        else:
            await callback.answer("Заполните все разделы перед закрытием", show_alert=True)
    await callback.answer()


@router.message(UserFlow.expenses_ship)
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
    data = await state.get_data()
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
        else:
            chosen_ship = text.title()
    else:
        chosen_ship = text.title()

    expenses["ship"] = chosen_ship
    await state.update_data(expenses_data=expenses)
    await message.answer(f"Судно зафиксировано: {chosen_ship}.")
    await go_to_next_expenses_step(message, state, "ship")


@router.message(UserFlow.expenses_holds)
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


@router.message(UserFlow.expenses_transport)
async def expenses_transport_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "transport")


@router.message(UserFlow.expenses_foreman)
async def expenses_foreman_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "foreman")


@router.message(UserFlow.expenses_workers)
async def expenses_workers_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "workers")


@router.message(UserFlow.expenses_aux)
async def expenses_aux_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "aux")


@router.message(UserFlow.expenses_food)
async def expenses_food_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "food")


@router.message(UserFlow.expenses_taxi)
async def expenses_taxi_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "taxi")


@router.message(UserFlow.expenses_other)
async def expenses_other_input(message: Message, state: FSMContext) -> None:
    await process_expense_amount(message, state, "other")


@router.message(UserFlow.expenses_review)
async def expenses_review_navigation(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    if text == "в меню":
        await go_to_shift_menu(message, state)
        return
    if text == "⬅ назад":
        await go_to_previous_expenses_step(message, state, "other")
        return
    await message.answer("Для завершения используйте кнопки подтверждения ниже.")


@router.callback_query(F.data == "expenses:edit", UserFlow.expenses_review)
async def expenses_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await prompt_expenses_ship(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "expenses:confirm", UserFlow.expenses_review)
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
    await state.set_state(UserFlow.shift_menu)
    await callback.message.answer(
        "Раздел «Расходы смены» сохранён.",
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )
    await callback.answer("Расходы сохранены")


@router.message(UserFlow.crew_select_driver)
async def crew_select_driver(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    lower_text = text.lower()
    if lower_text == "отмена":
        await state.set_state(UserFlow.shift_menu)
        data = await state.get_data()
        active_shift: ActiveShift = data.get("active_shift")
        if active_shift:
            await message.answer(
                "Возвращаю в меню смены.",
                reply_markup=shift_menu_keyboard(active_shift.sections),
            )
        else:
            await message.answer("Возврат в меню.", reply_markup=ReplyKeyboardRemove())
        return

    drivers = await sheets_service.get_drivers_directory()
    if text not in drivers:
        await message.answer("Пожалуйста, выберите водителя из списка.")
        return

    await state.update_data(selected_driver=text, selected_workers=[])
    await state.set_state(UserFlow.crew_select_workers)
    workers = await sheets_service.get_workers_directory()
    if not workers:
        await state.set_state(UserFlow.shift_menu)
        await message.answer(
            "Справочник рабочих пуст. Обратитесь к администратору.",
            reply_markup=shift_menu_keyboard(active_shift.sections),
        )
        return
    await message.answer(
        "Теперь выберите рабочих. Можно выбрать несколько имён подряд, после чего нажмите «Готово».",
        reply_markup=crew_workers_keyboard(workers),
    )


@router.message(UserFlow.crew_select_workers)
async def crew_select_workers(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    lower_text = text.lower()
    if lower_text == "отмена":
        await state.set_state(UserFlow.shift_menu)
        data = await state.get_data()
        active_shift: ActiveShift = data.get("active_shift")
        if active_shift:
            await message.answer(
                "Возвращаю в меню смены.",
                reply_markup=shift_menu_keyboard(active_shift.sections),
            )
        return

    workers_directory = await sheets_service.get_workers_directory()
    data = await state.get_data()
    selected_workers: List[str] = data.get("selected_workers", [])

    if lower_text == "готово":
        if not selected_workers:
            await message.answer("Выберите хотя бы одного рабочего.")
            return
        await state.set_state(UserFlow.crew_confirm)
        driver = data.get("selected_driver")
        summary = "\n".join([f"• {name}" for name in selected_workers])
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Сохранить", callback_data="crew:save")],
                [InlineKeyboardButton(text="Изменить", callback_data="crew:restart")],
            ]
        )
        await message.answer(
            f"Проверьте состав:\nВодитель: {driver}\nРабочие:\n{summary}",
            reply_markup=keyboard,
        )
        return

    if text not in workers_directory:
        await message.answer("Выбирайте рабочих из списка или нажмите «Готово».")
        return

    if text in selected_workers:
        await message.answer("Этот рабочий уже добавлен.")
        return

    selected_workers.append(text)
    await state.update_data(selected_workers=selected_workers)
    await message.answer("Добавлено. Продолжайте выбор или нажмите «Готово».")


@router.callback_query(F.data == "crew:restart", UserFlow.crew_confirm)
async def crew_restart(callback: CallbackQuery, state: FSMContext) -> None:
    drivers = await sheets_service.get_drivers_directory()
    if not drivers:
        await callback.answer("Нет данных о водителях", show_alert=True)
        return
    await state.set_state(UserFlow.crew_select_driver)
    await state.update_data(selected_workers=[], selected_driver="")
    await callback.message.answer(
        "Выберите водителя:", reply_markup=crew_driver_keyboard(drivers)
    )
    await callback.answer()


@router.callback_query(F.data == "crew:save", UserFlow.crew_confirm)
async def crew_save(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: ActiveShift = data.get("active_shift")
    if not active_shift:
        await callback.answer("Нет активной смены", show_alert=True)
        return
    driver = data.get("selected_driver", "")
    workers = data.get("selected_workers", [])
    await sheets_service.save_crew(active_shift.rows["crew"], driver, workers)
    active_shift.sections["crew"] = True
    await state.update_data(active_shift=active_shift)
    await state.set_state(UserFlow.shift_menu)
    await callback.message.answer(
        "Состав бригады сохранён.",
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )
    await callback.answer()


@router.callback_query(F.data == "shift_close:cancel", UserFlow.closing_confirmation)
async def close_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: ActiveShift = data.get("active_shift")
    await state.set_state(UserFlow.shift_menu)
    if active_shift:
        await callback.message.answer(
            "Хорошо, возвращаю в меню.",
            reply_markup=shift_menu_keyboard(active_shift.sections),
        )
    await callback.answer()


@router.callback_query(F.data == "shift_close:confirm", UserFlow.closing_confirmation)
async def close_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: ActiveShift = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена отсутствует", show_alert=True)
        return

    if not all(active_shift.sections.values()):
        await callback.answer("Не все разделы заполнены", show_alert=True)
        await state.set_state(UserFlow.shift_menu)
        await callback.message.answer(
            "Заполните все разделы смены.",
            reply_markup=shift_menu_keyboard(active_shift.sections),
        )
        return

    await sheets_service.finalize_shift(active_shift.rows["expenses"])
    user_row = data.get("user_row")
    if user_row:
        await sheets_service.update_closure_info(user_row)

    await state.set_state(UserFlow.dashboard)
    await callback.message.answer("Смена успешно закрыта!", reply_markup=ReplyKeyboardRemove())
    await send_dashboard(callback.message, state)
    await state.update_data(active_shift=None, selected_driver="", selected_workers=[])
    await callback.answer("Смена закрыта")


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Доступные команды:\n/start — запуск и проверка регистрации\n"
        "/help — справка"
    )


async def main() -> None:
    logging.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

