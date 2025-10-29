"""Обработчики главной панели и запуска меню смены."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.handlers.shift_menu import (
    ShiftState,
    render_shift_menu,
    reset_shift_session,
)
from bot.keyboards.dashboard import START_SHIFT_BUTTON, dashboard_keyboard
from bot.utils.flash import flash_message
from features.utils.locks import acquire_user_lock, release_user_lock
from services.sheets import ShiftAlreadyOpenedError, SheetsService, UserProfile

router = Router(name="dashboard")

logger = logging.getLogger(__name__)
_service: SheetsService | None = None


def _get_service() -> SheetsService:
    """Возвращает (или создаёт) экземпляр сервиса работы с таблицей."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


async def _load_profile(service: SheetsService, user_id: int) -> UserProfile | None:
    """Получает профиль пользователя, обрабатывая сетевые ошибки."""

    try:
        return await asyncio.to_thread(service.get_user_profile, user_id, required=False)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось получить профиль пользователя (user_id=%s)", user_id)
        return None


async def show_dashboard(
    message: types.Message,
    *,
    service: SheetsService | None = None,
    state: FSMContext | None = None,
) -> None:
    """Отправляет главную панель с приветствием и статистикой по сменам."""

    sheets = service or _get_service()
    user_id = message.from_user.id
    profile = await _load_profile(sheets, user_id)

    if profile is None:
        await message.answer(
            "Не удалось открыть главное меню. Попробуйте команду /menu позже или обратитесь к координатору."
        )
        return

    if state is not None:
        await state.set_state(ShiftState.IDLE)
    reset_shift_session(user_id)

    fio_text = profile.fio.strip() or profile.fio_compact.strip() or str(user_id)
    suffix = "" if fio_text.endswith(".") else "."

    lines = [
        "🗂 Главная панель",
        f"Здравствуйте, {fio_text}{suffix}",
        "",
        f"Смен закрыто: {profile.closed_shifts}",
        "",
        "Чтобы начать новую смену, воспользуйтесь кнопкой ниже.",
        "Если нужна справка, откройте раздел «Руководство» в закреплённых материалах бота.",
    ]

    await message.answer("\n".join(lines), reply_markup=dashboard_keyboard())


@router.message(Command("menu"))
async def handle_menu_command(message: types.Message, state: FSMContext) -> None:
    """Команда /menu для ручного открытия главной панели."""

    await show_dashboard(message, state=state)


async def _open_shift(
    message: types.Message,
    state: FSMContext,
    *,
    service: SheetsService,
) -> None:
    """Создаёт рабочую строку смены и отображает меню смены."""

    user_id = message.from_user.id

    await flash_message(message, "Загружаю…", ttl=2.0)

    try:
        locked_today, existing_row = await asyncio.to_thread(
            service.check_today_shift_lock, user_id
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось проверить блокировку смены (user_id=%s)", user_id
        )
        await message.answer(
            "Не удалось подготовить смену. Попробуйте снова позже или обратитесь к координатору."
        )
        return

    if locked_today:
        await message.answer(
            "Смена на сегодня уже закрыта. Повторное заполнение будет доступно завтра."
        )
        return

    lock = await acquire_user_lock(user_id)
    if lock is None:
        await message.answer(
            "Предыдущее действие ещё выполняется. Дождитесь завершения обработки и попробуйте снова."
        )
        return

    row_index = existing_row
    try:
        if row_index is None:
            row_index = await asyncio.to_thread(service.open_shift_for_user, user_id)
    except ShiftAlreadyOpenedError:
        await message.answer(
            "Смена на сегодня уже закрыта. Повторное заполнение будет доступно завтра."
        )
        return
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось подготовить строку смены (user_id=%s)", user_id
        )
        await message.answer(
            "Не удалось подготовить смену. Попробуйте снова позже или обратитесь к координатору."
        )
        return
    finally:
        release_user_lock(lock)

    try:
        logger.info(
            "Открываем меню смены (user_id=%s, row=%s)",
            user_id,
            row_index,
        )
        await render_shift_menu(
            message,
            user_id,
            row_index,
            service=service,
            state=state,
            delete_trigger_message=True,
            show_loading=False,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Сбой при отображении меню смены (user_id=%s, row=%s)",
            user_id,
            row_index,
            exc_info=True,
        )
        await message.answer(
            "Не удалось открыть меню смены. Попробуйте снова или обратитесь к координатору."
        )


@router.message(F.text == START_SHIFT_BUTTON)
async def handle_start_shift(message: types.Message, state: FSMContext) -> None:
    """Обработчик кнопки запуска оформления смены."""

    service = _get_service()
    await _open_shift(message, state, service=service)
