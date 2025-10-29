"""Обновлённый сценарий регистрации пользователей."""

from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.auth import (
    CANCEL_BUTTON,
    CONFIRM_BUTTON,
    RETRY_BUTTON,
    SKIP_BUTTON,
    START_BUTTON,
    confirm_retry_kb,
    skip_button_kb,
    start_registration_kb,
)
from bot.utils.flash import flash_message
from bot.validators.name import validate_name
from bot.handlers.dashboard import show_dashboard

# Совместимый псевдоним для существующих тестов и обработчиков.
show_menu = show_dashboard
from services.env import require_env
from services.sheets import SheetsService

router = Router(name="registration")

_SERVICE: SheetsService | None = None


class RegistrationState(StatesGroup):
    """Состояния FSM регистрации по шагам UX."""

    start = State()
    last_name = State()
    first_name = State()
    patronymic = State()
    confirm = State()


async def _safe_delete_message(bot: Bot, chat_id: int, message_id: int | None) -> None:
    """Безопасно удаляет сообщение, игнорируя ошибки Telegram."""

    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass


async def _clear_data_message(
    state: FSMContext, key: str, *, bot: types.Bot, chat_id: int
) -> None:
    """Удаляет сохранённое сообщение по ключу (prompt/error/confirm)."""

    data = await state.get_data()
    message_id = data.get(key)
    if message_id:
        await _safe_delete_message(bot, chat_id, message_id)
    await state.update_data(**{key: None})


async def _clear_duplicate_alert(state: FSMContext, *, bot: Bot, chat_id: int) -> None:
    """Удаляет сообщение о конфликте ФИО, если оно есть."""

    await _clear_data_message(state, "duplicate_id", bot=bot, chat_id=chat_id)


def _get_service() -> SheetsService:
    """Возвращает экземпляр сервиса работы с таблицами."""

    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SheetsService()
    return _SERVICE


def _get_spreadsheet_id() -> str:
    """Получает идентификатор таблицы из окружения."""

    return require_env("SPREADSHEET_ID")


async def _register_user_input(state: FSMContext, message_id: int) -> None:
    """Добавляет идентификатор пользовательского сообщения в список для очистки."""

    data = await state.get_data()
    ids: list[int] = list(data.get("input_ids", []))
    ids.append(message_id)
    await state.update_data(input_ids=ids)


async def _clear_user_inputs(
    state: FSMContext, *, bot: types.Bot, chat_id: int
) -> None:
    """Удаляет все пользовательские сообщения, сохранённые для текущего шага."""

    data = await state.get_data()
    ids: list[int] = list(data.get("input_ids", []))
    if not ids:
        return
    for message_id in ids:
        await _safe_delete_message(bot, chat_id, message_id)
    await state.update_data(input_ids=[])


async def _store_prompt(
    source_message: types.Message, state: FSMContext, text: str, *, reply_markup: Any | None = None
) -> types.Message:
    """Отправляет новый вопрос и сохраняет его идентификатор в состоянии."""

    prompt = await source_message.answer(text, reply_markup=reply_markup)
    await state.update_data(prompt_id=prompt.message_id, input_ids=[])
    return prompt


async def _handle_text_step(
    message: types.Message,
    state: FSMContext,
    *,
    validator_message: str,
    error_text: str,
    next_state: State,
    data_key: str,
    reply_markup: Any | None = None,
) -> None:
    """Обрабатывает стандартный шаг с текстовым вводом."""

    bot = message.bot
    chat_id = message.chat.id
    await _register_user_input(state, message.message_id)
    try:
        value = validate_name(message.text or "")
    except ValueError as exc:
        await _clear_data_message(state, "error_id", bot=bot, chat_id=chat_id)
        error_message = await message.answer(f"{error_text}: {exc}")
        await state.update_data(error_id=error_message.message_id)
        return

    await state.update_data(**{data_key: value})
    await _clear_user_inputs(state, bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "prompt_id", bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "error_id", bot=bot, chat_id=chat_id)
    await _clear_duplicate_alert(state, bot=bot, chat_id=chat_id)
    await state.set_state(next_state)
    await _store_prompt(message, state, validator_message, reply_markup=reply_markup)


async def _show_confirmation(message: types.Message, state: FSMContext) -> None:
    """Формирует экран подтверждения REG-11."""

    data = await state.get_data()
    bot = message.bot
    chat_id = message.chat.id
    await _clear_data_message(state, "prompt_id", bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "error_id", bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "confirm_id", bot=bot, chat_id=chat_id)
    await _clear_duplicate_alert(state, bot=bot, chat_id=chat_id)
    await _clear_user_inputs(state, bot=bot, chat_id=chat_id)

    fio = " ".join(
        part
        for part in (
            data.get("last_name", ""),
            data.get("first_name", ""),
            data.get("patronymic", ""),
        )
        if part
    )
    confirm_message = await message.answer(
        f"Проверьте данные:\n<b>{fio}</b>\n\nНажмите «Подтвердить» или «Ввести заново».",
        reply_markup=confirm_retry_kb(),
        parse_mode="HTML",
    )
    await state.update_data(confirm_id=confirm_message.message_id)
    await state.set_state(RegistrationState.confirm)


@router.message(CommandStart())
async def handle_start(message: types.Message, state: FSMContext) -> None:
    """Точка входа: проверка статуса и запуск регистрации."""

    await flash_message(message, "🔍 Проверяю доступ…", ttl=1.5)

    user_id = message.from_user.id
    service = _get_service()
    spreadsheet_id = _get_spreadsheet_id()

    try:
        row, status = await asyncio.to_thread(
            service.find_row_by_telegram_id, spreadsheet_id, user_id
        )
    except PermissionError:
        await message.answer("Ваш доступ заблокирован. Обратитесь к координатору для уточнения статуса.")
        return
    except Exception:  # noqa: BLE001
        await message.answer("Не удалось проверить регистрацию. Попробуйте повторить попытку позже.")
        return

    status_normalized = (status or "").strip().casefold() if status else ""

    if status_normalized == "архив":
        await message.answer("Ваш доступ отключён. Обратитесь к координатору (статус: Архив).")
        return

    if status_normalized == "ban":
        await message.answer("Ваш доступ заблокирован. Обратитесь к координатору для уточнения статуса.")
        return

    profile = None
    if row and status_normalized not in {"архив", "ban"}:
        try:
            profile = await asyncio.to_thread(
                service.get_user_profile,
                user_id,
                spreadsheet_id,
                required=False,
            )
        except RuntimeError:
            profile = None
        except Exception:  # noqa: BLE001
            await message.answer(
                "Не удалось проверить регистрацию. Попробуйте повторить попытку позже."
            )
            return

    if profile:
        reg03 = await message.answer(
            "Добро пожаловать! Вы уже зарегистрированы. Открываю панель."
        )
        await show_menu(message, service=service, state=state)
        await _safe_delete_message(message.bot, message.chat.id, reg03.message_id)
        return

    await _clear_duplicate_alert(state, bot=message.bot, chat_id=message.chat.id)
    await state.clear()
    await state.set_state(RegistrationState.start)
    await _store_prompt(
        message,
        state,
        "Чтобы начать регистрацию, нажмите кнопку ниже.",
        reply_markup=start_registration_kb(),
    )


@router.message(RegistrationState.start, F.text == START_BUTTON)
async def start_registration(message: types.Message, state: FSMContext) -> None:
    """Запускает ввод фамилии после нажатия reply-кнопки."""

    await _register_user_input(state, message.message_id)
    bot = message.bot
    chat_id = message.chat.id
    await _clear_user_inputs(state, bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "prompt_id", bot=bot, chat_id=chat_id)
    await _clear_duplicate_alert(state, bot=bot, chat_id=chat_id)
    await state.set_state(RegistrationState.last_name)
    await state.update_data(
        last_name="",
        first_name="",
        patronymic="",
        prompt_id=None,
        error_id=None,
        duplicate_id=None,
    )
    await _store_prompt(
        message,
        state,
        "Введите вашу Фамилию (только буквы).",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@router.message(RegistrationState.start, F.text == CANCEL_BUTTON)
@router.message(RegistrationState.last_name, F.text == CANCEL_BUTTON)
@router.message(RegistrationState.first_name, F.text == CANCEL_BUTTON)
@router.message(RegistrationState.patronymic, F.text == CANCEL_BUTTON)
@router.message(RegistrationState.confirm, F.text == CANCEL_BUTTON)
async def cancel_registration(message: types.Message, state: FSMContext) -> None:
    """Отменяет регистрацию и очищает служебные сообщения."""

    bot = message.bot
    chat_id = message.chat.id
    await _register_user_input(state, message.message_id)
    await _clear_user_inputs(state, bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "prompt_id", bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "error_id", bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "confirm_id", bot=bot, chat_id=chat_id)
    await _clear_duplicate_alert(state, bot=bot, chat_id=chat_id)
    await state.clear()
    await message.answer(
        "Регистрация отменена. Для повторного запуска используйте команду /start.",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@router.message(RegistrationState.last_name)
async def process_last_name(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод фамилии (REG-05/06)."""

    await _handle_text_step(
        message,
        state,
        validator_message="Введите ваше Имя (только буквы).",
        error_text="Некорректная фамилия",
        next_state=RegistrationState.first_name,
        data_key="last_name",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@router.message(RegistrationState.first_name)
async def process_first_name(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод имени (REG-07/08)."""

    await _handle_text_step(
        message,
        state,
        validator_message="Введите ваше Отчество (если нет — нажмите «Пропустить»).",
        error_text="Некорректное имя",
        next_state=RegistrationState.patronymic,
        data_key="first_name",
        reply_markup=skip_button_kb(),
    )


@router.message(RegistrationState.patronymic)
async def process_patronymic(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод отчества (REG-09/10)."""

    bot = message.bot
    chat_id = message.chat.id
    text = (message.text or "").strip()
    await _register_user_input(state, message.message_id)
    if text.casefold() == SKIP_BUTTON.casefold():
        await state.update_data(patronymic="")
        await _clear_user_inputs(state, bot=bot, chat_id=chat_id)
        await _show_confirmation(message, state)
        return
    try:
        value = validate_name(text)
    except ValueError as exc:
        await _clear_data_message(state, "error_id", bot=bot, chat_id=chat_id)
        error_message = await message.answer(f"Некорректное отчество: {exc}")
        await state.update_data(error_id=error_message.message_id)
        return

    await state.update_data(patronymic=value)
    await _clear_user_inputs(state, bot=bot, chat_id=chat_id)
    await _clear_duplicate_alert(state, bot=bot, chat_id=chat_id)
    await _show_confirmation(message, state)


@router.message(RegistrationState.confirm, F.text == RETRY_BUTTON)
async def retry_registration(message: types.Message, state: FSMContext) -> None:
    """Возвращает пользователя к началу ввода ФИО."""

    bot = message.bot
    chat_id = message.chat.id
    await _register_user_input(state, message.message_id)
    await _clear_user_inputs(state, bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "confirm_id", bot=bot, chat_id=chat_id)
    await _clear_duplicate_alert(state, bot=bot, chat_id=chat_id)
    await state.set_state(RegistrationState.last_name)
    await state.update_data(
        prompt_id=None,
        error_id=None,
        last_name="",
        first_name="",
        patronymic="",
        input_ids=[],
    )
    await _store_prompt(
        message,
        state,
        "Введите вашу Фамилию (только буквы).",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@router.message(RegistrationState.confirm, F.text == CONFIRM_BUTTON)
async def confirm_registration(message: types.Message, state: FSMContext) -> None:
    """Подтверждает данные и сохраняет запись в таблице."""

    bot = message.bot
    chat_id = message.chat.id
    await _register_user_input(state, message.message_id)
    await _clear_user_inputs(state, bot=bot, chat_id=chat_id)
    await _clear_data_message(state, "confirm_id", bot=bot, chat_id=chat_id)

    data = await state.get_data()
    last_name = data.get("last_name", "")
    first_name = data.get("first_name", "")
    patronymic = data.get("patronymic", "")

    if not last_name or not first_name:
        await message.answer(
            "Данные регистрации потеряны. Нажмите /start и попробуйте снова.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        await state.clear()
        return

    service = _get_service()
    spreadsheet_id = _get_spreadsheet_id()

    duplicate_exists = await asyncio.to_thread(
        service.fio_duplicate_exists, spreadsheet_id, last_name, first_name, patronymic
    )
    if duplicate_exists:
        await _clear_duplicate_alert(state, bot=bot, chat_id=chat_id)
        duplicate_message = await message.answer(
            "Пользователь с таким ФИО уже зарегистрирован. Уточните данные или обратитесь к координатору."
        )
        await state.update_data(duplicate_id=duplicate_message.message_id)
        return

    user_id = message.from_user.id
    try:
        await asyncio.to_thread(
            service.upsert_registration_row,
            spreadsheet_id,
            user_id,
            last_name,
            first_name,
            patronymic,
        )
    except PermissionError:
        await message.answer("Ваш доступ отключён. Обратитесь к координатору.")
        return
    except Exception:  # noqa: BLE001
        await message.answer("Временная ошибка при сохранении. Попробуйте позже.")
        return

    await _clear_duplicate_alert(state, bot=bot, chat_id=chat_id)
    await state.clear()
    await message.answer(
        "Регистрация завершена. Статус: Активен. Открываю панель.",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await flash_message(message, "✅ Регистрация завершена", ttl=2.0)

    try:
        await asyncio.to_thread(service.get_user_profile, user_id)
    except Exception:  # noqa: BLE001
        await message.answer(
            "Профиль сохранён, но открыть меню не удалось. Воспользуйтесь командой /menu немного позже."
        )
        return

    await show_menu(message, service=service, state=state)
