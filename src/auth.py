from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from .dependencies import sheets_service
from .main import send_dashboard

router = Router()


class AuthStates(StatesGroup):
    """Состояния процесса регистрации."""

    last_name = State()
    first_name = State()
    middle_name = State()
    confirm = State()


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
    await state.set_state(AuthStates.last_name)
    await state.update_data(last_name="", first_name="", middle_name="")
    await callback.message.answer(
        "Введите вашу фамилию.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer()


@router.message(AuthStates.last_name)
async def registration_last_name(message: Message, state: FSMContext) -> None:
    last_name = (message.text or "").strip()
    if not last_name:
        await message.answer("Пожалуйста, введите фамилию.")
        return
    await state.update_data(last_name=last_name)
    await state.set_state(AuthStates.first_name)
    await message.answer(
        "Введите ваше имя.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AuthStates.first_name)
async def registration_first_name(message: Message, state: FSMContext) -> None:
    first_name = (message.text or "").strip()
    if not first_name:
        await message.answer("Имя не может быть пустым. Введите имя.")
        return
    await state.update_data(first_name=first_name)
    await state.set_state(AuthStates.middle_name)
    await message.answer(
        "Введите ваше отчество или нажмите «Пропустить», если его нет.",
        reply_markup=skip_keyboard(),
    )


@router.message(AuthStates.middle_name)
async def registration_middle_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
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
    await state.set_state(AuthStates.confirm)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "registration:restart", AuthStates.confirm)
async def registration_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AuthStates.last_name)
    await state.update_data(last_name="", first_name="", middle_name="")
    await callback.message.answer(
        "Хорошо, давайте начнём заново. Введите вашу фамилию.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer()


@router.callback_query(F.data == "registration:confirm", AuthStates.confirm)
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

