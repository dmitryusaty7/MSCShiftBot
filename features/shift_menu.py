"""Меню оформления смены и статусы разделов."""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from features.utils.locks import acquire_user_lock, release_user_lock
from features.utils.messaging import safe_delete, send_progress
from services.sheets import SheetsService

router = Router()
_service: SheetsService | None = None
logger = logging.getLogger(__name__)


def _get_service() -> SheetsService:
    """Ленивая инициализация сервиса таблиц."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


def _resolve_service(service: SheetsService | None) -> SheetsService:
    """Возвращает переданный сервис или создаёт общий экземпляр."""

    global _service
    if service is not None:
        _service = service
        return service
    return _get_service()


# ---- разделы / пиктограммы ----
BTN_EXPENSES_LABEL = "🧾 Расходы"
BTN_MATERIALS_LABEL = "📦 Материалы"
BTN_CREW_LABEL = "👥 Бригада"
BTN_BACK = "⬅ Назад в главное меню"

# ---- стиль статусов: 'emoji' | 'traffic' | 'text'
STATUS_STYLE = "emoji"

STATUS_SETS = {
    "emoji": {"done": "✅ готово", "todo": "✍️ заполнить"},
    "traffic": {"done": "🟢 готово", "todo": "🟠 заполнить"},
    "text": {"done": "[готово]", "todo": "[заполнить]"},
}


def status_badge(done: bool) -> str:
    """Возвращает подпись статуса с учётом выбранного стиля."""

    style = STATUS_SETS.get(STATUS_STYLE, STATUS_SETS["emoji"])
    return style["done"] if done else style["todo"]


def _line(label: str, done: bool) -> str:
    """Строка с названием раздела и статусом заполнения."""

    return f"{label} — {status_badge(done)}"


def _keyboard(expenses_ok: bool, materials_ok: bool, crew_ok: bool) -> types.ReplyKeyboardMarkup:
    """Собирает клавиатуру меню смены."""

    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text=_line(BTN_EXPENSES_LABEL, expenses_ok))
    keyboard.button(text=_line(BTN_MATERIALS_LABEL, materials_ok))
    keyboard.button(text=_line(BTN_CREW_LABEL, crew_ok))
    keyboard.adjust(1, 1, 1)
    keyboard.button(text=BTN_BACK)
    keyboard.adjust(1)
    return keyboard.as_markup(resize_keyboard=True)


async def render_shift_menu(
    message: types.Message,
    user_id: int,
    row: int | None,
    service: SheetsService | None = None,
    *,
    delete_trigger_message: bool = True,
    show_progress: bool = True,
) -> None:
    """Отображает меню смены, создавая строку при необходимости."""

    sheets = _resolve_service(service)

    if delete_trigger_message:
        await safe_delete(message)

    progress_message = (
        await send_progress(message, "⏳ Проверяю статус смены. Подождите…")
        if show_progress
        else None
    )

    row_index = row
    progress: dict[str, bool] | None = None
    lock = None
    try:
        if row_index is None:
            lock = await acquire_user_lock(user_id)
            if lock is None:
                await message.answer(
                    "Предыдущее действие ещё выполняется. Повторите попытку через несколько секунд."
                )
                return

            try:
                row_index = sheets.open_shift_for_user(user_id)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Не удалось открыть строку смены (user_id=%s)", user_id
                )
                await message.answer(
                    "Не удалось подготовить смену. Попробуйте позже или обратитесь к координатору."
                )
                return

        progress = sheets.get_shift_progress(user_id, row_index)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось получить прогресс смены (user_id=%s)", user_id)
        await message.answer(
            "Не удалось открыть меню смены. Попробуйте позже или обратитесь к координатору."
        )
        return
    finally:
        if lock is not None:
            release_user_lock(lock)
        await safe_delete(progress_message)

    if progress is None or row_index is None:
        return

    text = (
        "выберите раздел для заполнения.\n"
        "в каждом нужно указать данные по текущей смене."
    )
    await message.answer(
        text,
        reply_markup=_keyboard(
            expenses_ok=progress["expenses"],
            materials_ok=progress["materials"],
            crew_ok=progress["crew"],
        ),
    )


@router.message(lambda msg: msg.text == BTN_BACK)
async def back_to_main(message: types.Message) -> None:
    """Возвращает пользователя в основное меню."""

    from features.main_menu import show_menu

    await safe_delete(message)
    await show_menu(message)


@router.message(lambda msg: msg.text.startswith(BTN_EXPENSES_LABEL))
async def go_expenses(message: types.Message, state: FSMContext) -> None:
    """Переходит в сценарий заполнения раздела «Расходы»."""

    from features.expenses import start_expenses

    await start_expenses(message, state)


@router.message(lambda msg: msg.text.startswith(BTN_MATERIALS_LABEL))
async def go_materials(message: types.Message) -> None:
    """Заглушка раздела «Материалы» до подключения сценария."""

    await safe_delete(message)
    await message.answer("раздел «материалы» подключим следующим этапом.")


@router.message(lambda msg: msg.text.startswith(BTN_CREW_LABEL))
async def go_crew(message: types.Message) -> None:
    """Заглушка раздела «Бригада» до подключения сценария."""

    await safe_delete(message)
    await message.answer("раздел «бригада» подключим следующим этапом.")
