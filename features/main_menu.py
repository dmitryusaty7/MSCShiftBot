"""Основное меню бота со стартовой панелью смен."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from features.shift_menu import render_shift_menu
from features.utils.locks import acquire_user_lock, release_user_lock
from features.utils.messaging import safe_delete, send_progress
from services.sheets import (
    ShiftAlreadyOpenedError,
    SheetsService,
    UserProfile,
)

router = Router()
_service: SheetsService | None = None
logger = logging.getLogger(__name__)


def _get_service() -> SheetsService:
    """Ленивая инициализация сервиса работы с таблицами."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


START_SHIFT_BTN = "🚀 Начать оформление смены"


def _menu_keyboard() -> types.ReplyKeyboardMarkup:
    """Формирует клавиатуру главного меню."""

    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text=START_SHIFT_BTN)
    keyboard.adjust(1)
    return keyboard.as_markup(resize_keyboard=True)


async def show_menu(
    message: types.Message,
    service: SheetsService | None = None,
    *,
    state: FSMContext | None = None,
) -> None:
    """Отправляет пользователю основную панель с приветствием."""

    user_id = message.from_user.id
    sheets = service or _get_service()

    await safe_delete(message)
    progress_message = await send_progress(
        message, "⏳ Загружаю главное меню, подождите…"
    )

    try:
        profile: UserProfile = await asyncio.to_thread(
            sheets.get_user_profile, user_id
        )
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось получить профиль для меню (user_id=%s)", user_id)
        await safe_delete(progress_message)
        await message.answer(
            "Не удалось открыть главное меню. Попробуйте команду /menu позже "
            "или обратитесь к координатору."
        )
        return

    await safe_delete(progress_message)

    fio_text = profile.fio.strip()
    suffix = "" if fio_text.endswith(".") else "."
    text = (
        f"Здравствуйте, {fio_text}{suffix}\n\n"
        f"Смен закрыто: {profile.closed_shifts}"
    )
    if state is not None:
        from features.shift_menu import ShiftState, reset_shift_session

        await state.set_state(ShiftState.IDLE)
        reset_shift_session(user_id)
    await message.answer(text, reply_markup=_menu_keyboard())


@router.message(Command("menu"))
async def handle_menu_command(message: types.Message, state: FSMContext) -> None:
    """Команда /menu для ручного открытия панели."""

    await show_menu(message, state=state)


@router.message(lambda msg: msg.text == START_SHIFT_BTN)
async def start_shift(message: types.Message, state: FSMContext) -> None:
    """Создаёт или подготавливает рабочую строку смены для пользователя."""

    user_id = message.from_user.id
    service = _get_service()
    lock = await acquire_user_lock(user_id)
    if lock is None:
        await safe_delete(message)
        await message.answer(
            "Предыдущее действие ещё выполняется. Дождитесь завершения обработки и попробуйте снова."
        )
        return

    await safe_delete(message)
    progress_message = await send_progress(
        message, "⏳ Подготавливаю смену. Пожалуйста, подождите…"
    )

    try:
        try:
            locked, _ = await asyncio.to_thread(
                service.check_today_shift_lock, user_id
            )
            if locked:
                await safe_delete(progress_message)
                progress_message = None
                await message.answer(
                    "Смена уже закрыта сегодня. Новую смену можно открыть завтра."
                )
                return
            row_index = await asyncio.to_thread(
                service.open_shift_for_user, user_id
            )
        except ShiftAlreadyOpenedError:
            await safe_delete(progress_message)
            progress_message = None
            await message.answer(
                "Смена уже закрыта сегодня. Новую смену можно открыть завтра."
            )
            return
        except Exception:  # noqa: BLE001
            logger.exception(
                "Не удалось подготовить смену (user_id=%s)", user_id
            )
            await safe_delete(progress_message)
            progress_message = None
            await message.answer(
                "Не удалось подготовить смену. Попробуйте снова позже или обратитесь к координатору."
            )
            return
        await safe_delete(progress_message)
        progress_message = None
        await render_shift_menu(
            message,
            user_id,
            row_index,
            service=service,
            state=state,
            delete_trigger_message=False,
            show_progress=True,
        )
    finally:
        await safe_delete(progress_message)
        release_user_lock(lock)
