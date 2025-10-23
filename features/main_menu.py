"""Основное меню бота со стартовой панелью смен."""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from services.sheets import SheetsService, UserProfile

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
    message: types.Message, service: SheetsService | None = None
) -> None:
    """Отправляет пользователю основную панель с приветствием."""

    user_id = message.from_user.id
    sheets = service or _get_service()

    try:
        profile: UserProfile = sheets.get_user_profile(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось получить профиль для меню (user_id=%s)", user_id)
        await message.answer(
            "Не удалось открыть главное меню. Попробуйте команду /menu позже "
            "или обратитесь к координатору."
        )
        return

    text = (
        f"Здравствуйте, {profile.fio}.\n\n"
        f"Вы закрыли {profile.closed_shifts} смен."
    )
    await message.answer(text, reply_markup=_menu_keyboard())


@router.message(Command("menu"))
async def handle_menu_command(message: types.Message) -> None:
    """Команда /menu для ручного открытия панели."""

    await show_menu(message)


@router.message(lambda msg: msg.text == START_SHIFT_BTN)
async def start_shift(message: types.Message) -> None:
    """Создаёт или подготавливает рабочую строку смены для пользователя."""

    user_id = message.from_user.id
    row_index = _get_service().open_shift_for_user(user_id)
    await message.answer(
        "Смена создана/подготовлена.\n"
        f"Рабочая строка №{row_index} синхронизирована во всех листах.\n"
        "Можно продолжать оформление в разделах.",
    )
