from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from .dependencies import sheets_service

router = Router()


@dataclass
class ActiveShift:
    """Структура данных активной смены."""

    rows: Dict[str, int]
    sections: Dict[str, bool]


class MainStates(StatesGroup):
    """Состояния основного сценария бота."""

    dashboard = State()
    shift_menu = State()
    closing_confirmation = State()


def dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать оформление смены", callback_data="dashboard:start")]
        ]
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


async def go_to_shift_menu(message: Message, state: FSMContext, notice: str = "Возвращаю в меню смены.") -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await state.set_state(MainStates.dashboard)
        await message.answer(
            "Активная смена не найдена. Возвращаю в личный кабинет.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await send_dashboard(message, state)
        return
    await state.set_state(MainStates.shift_menu)
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
    await state.set_state(MainStates.dashboard)
    await message.answer(text, reply_markup=dashboard_keyboard())


@router.callback_query(F.data == "dashboard:start", MainStates.dashboard)
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
    await state.set_state(MainStates.shift_menu)
    await callback.message.answer(
        "Смена создана. Используйте меню ниже для заполнения разделов.",
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )
    await callback.answer()


@router.callback_query(F.data == "shift_section:close", MainStates.shift_menu)
async def handle_close_section(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Смена не найдена", show_alert=True)
        return

    if all(active_shift.sections.values()):
        await state.set_state(MainStates.closing_confirmation)
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


@router.callback_query(F.data == "shift_close:cancel", MainStates.closing_confirmation)
async def close_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    await state.set_state(MainStates.shift_menu)
    if active_shift:
        await callback.message.answer(
            "Хорошо, возвращаю в меню.",
            reply_markup=shift_menu_keyboard(active_shift.sections),
        )
    await callback.answer()


@router.callback_query(F.data == "shift_close:confirm", MainStates.closing_confirmation)
async def close_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена отсутствует", show_alert=True)
        return

    if not all(active_shift.sections.values()):
        await callback.answer("Не все разделы заполнены", show_alert=True)
        await state.set_state(MainStates.shift_menu)
        await callback.message.answer(
            "Заполните все разделы смены.",
            reply_markup=shift_menu_keyboard(active_shift.sections),
        )
        return

    await sheets_service.finalize_shift(active_shift.rows["expenses"])
    user_row = data.get("user_row")
    if user_row:
        await sheets_service.update_closure_info(user_row)

    await state.set_state(MainStates.dashboard)
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
