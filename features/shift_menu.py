"""Меню оформления смены и статусы разделов."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from features.utils.locks import acquire_user_lock, release_user_lock
from features.utils.messaging import safe_delete, send_progress
from services.sheets import SheetsService
from services.env import group_notifications_enabled

router = Router()
_service: SheetsService | None = None
logger = logging.getLogger(__name__)

GROUP_CHAT_ID = -1003298300145


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
BTN_CLOSE_SHIFT = "🔒 Закрыть смену"

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


def _keyboard(
    expenses_ok: bool,
    materials_ok: bool,
    crew_ok: bool,
    *,
    close_enabled: bool,
) -> types.ReplyKeyboardMarkup:
    """Собирает клавиатуру меню смены."""

    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text=_line(BTN_EXPENSES_LABEL, expenses_ok))
    keyboard.button(text=_line(BTN_MATERIALS_LABEL, materials_ok))
    keyboard.button(text=_line(BTN_CREW_LABEL, crew_ok))
    if close_enabled:
        keyboard.button(text=BTN_CLOSE_SHIFT)
    keyboard.button(text=BTN_BACK)
    layout = [1, 1, 1]
    if close_enabled:
        layout.append(1)
    layout.append(1)
    keyboard.adjust(*layout)
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
                row_index = await asyncio.to_thread(
                    sheets.open_shift_for_user, user_id
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Не удалось открыть строку смены (user_id=%s)", user_id
                )
                await message.answer(
                    "Не удалось подготовить смену. Попробуйте позже или обратитесь к координатору."
                )
                return

        progress = await asyncio.to_thread(
            sheets.get_shift_progress, user_id, row_index
        )
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

    try:
        shift_closed = await asyncio.to_thread(
            sheets.is_shift_closed, row_index
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось проверить, закрыта ли смена (user_id=%s, row=%s)",
            user_id,
            row_index,
        )
        shift_closed = False

    close_enabled = all(progress.values()) and not shift_closed

    base_text = (
        "выберите раздел для заполнения.\n"
        "в каждом нужно указать данные по текущей смене."
    )
    if shift_closed:
        base_text += (
            "\n\nсмена уже закрыта. если нужно начать новую — вернитесь в главное меню."
        )
    await message.answer(
        base_text,
        reply_markup=_keyboard(
            expenses_ok=progress["expenses"],
            materials_ok=progress["materials"],
            crew_ok=progress["crew"],
            close_enabled=close_enabled,
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
async def go_materials(message: types.Message, state: FSMContext) -> None:
    """Переходит в сценарий заполнения раздела «Материалы»."""

    from features.materials import start_materials

    await start_materials(message, state)


@router.message(lambda msg: msg.text.startswith(BTN_CREW_LABEL))
async def go_crew(message: types.Message, state: FSMContext) -> None:
    """Переходит в сценарий заполнения раздела «Бригада»."""

    from features.crew import start_crew

    await start_crew(message, state)


def _format_number(value: int) -> str:
    """Форматирует число с пробелами в качестве разделителей тысяч."""

    return f"{value:,}".replace(",", " ")


def _format_date_for_summary(date_value: str) -> str:
    """Приводит дату в формат ДД.ММ.ГГГГ, если удаётся разобрать ISO-строку."""

    text = (date_value or "").strip()
    if not text:
        return "—"
    for pattern in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text


def build_group_report(brigadier: str, summary: dict[str, object]) -> str:
    """Формирует текст отчёта для группового чата."""

    expenses = summary.get("expenses", {}) if isinstance(summary, dict) else {}
    materials = summary.get("materials", {}) if isinstance(summary, dict) else {}

    date_text = _format_date_for_summary(str(summary.get("date", "")))
    ship = str(summary.get("ship", "")).strip() or "—"

    total_amount = 0
    if isinstance(expenses, dict):
        for key in ("driver", "brigadier", "workers", "aux", "food", "taxi", "other"):
            try:
                total_amount += int(expenses.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
    total_text = _format_number(total_amount)

    photos_link = "—"
    if isinstance(materials, dict):
        link_candidate = materials.get("photos_link")
        if isinstance(link_candidate, str) and link_candidate.strip():
            photos_link = link_candidate.strip()

    brigadier_line = brigadier.strip() if brigadier and brigadier.strip() else "—"

    lines = [
        "✅ Смена закрыта",
        "",
        f"👷‍♂️ Бригадир: {brigadier_line}",
        f"📅 Дата: {date_text}",
        f"🛳 Судно: {ship}",
        f"💰 Всего расходов: {total_text} ₽",
        f"📷 Фото: {photos_link}",
        "",
        "Спасибо за работу!",
    ]
    return "\n".join(lines)


@router.message(F.text == BTN_CLOSE_SHIFT)
async def close_shift(message: types.Message) -> None:
    """Обрабатывает закрытие смены и отправку сводки."""

    user_id = message.from_user.id
    sheets = _resolve_service(None)

    try:
        row = await asyncio.to_thread(
            sheets.get_shift_row_index_for_user, user_id
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось получить строку смены для закрытия (user_id=%s)",
            user_id,
        )
        await message.answer(
            "не удалось определить рабочую строку. попробуйте позже или обратитесь к координатору."
        )
        return

    if not row:
        await message.answer(
            "рабочая строка не найдена. начните смену заново через главное меню."
        )
        return

    try:
        already_closed = await asyncio.to_thread(sheets.is_shift_closed, row)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось проверить состояние закрытия смены (user_id=%s, row=%s)",
            user_id,
            row,
        )
        await message.answer(
            "не удалось проверить состояние смены. попробуйте позже."
        )
        return

    if already_closed:
        await message.answer("смена уже закрыта.")
        return

    try:
        progress = await asyncio.to_thread(
            sheets.get_shift_progress, user_id, row
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось получить прогресс смены перед закрытием (user_id=%s, row=%s)",
            user_id,
            row,
        )
        await message.answer(
            "не удалось проверить заполненность разделов. попробуйте позже."
        )
        return

    if not all(progress.values()):
        await message.answer(
            "не все разделы заполнены. заполните разделы и попробуйте снова."
        )
        return

    profile = None
    try:
        profile = await asyncio.to_thread(sheets.get_user_profile, user_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось получить профиль пользователя перед закрытием (user_id=%s)",
            user_id,
        )

    try:
        summary = await asyncio.to_thread(sheets.get_shift_summary, row)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось собрать сводку смены (user_id=%s, row=%s)",
            user_id,
            row,
        )
        await message.answer(
            "не удалось сформировать сводку. попробуйте позже."
        )
        return

    try:
        closed_now = await asyncio.to_thread(
            sheets.finalize_shift, user_id, row
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Ошибка при закрытии смены (user_id=%s, row=%s)", user_id, row
        )
        await message.answer(
            "не удалось закрыть смену. попробуйте позже или обратитесь к координатору."
        )
        return

    if not closed_now:
        await message.answer("смена уже закрыта.")
        return

    notifications_enabled = group_notifications_enabled()
    group_sent = False
    if notifications_enabled:
        crew_info = summary.get("crew") if isinstance(summary, dict) else None
        brigadier_name = ""
        if isinstance(crew_info, dict):
            name_candidate = crew_info.get("brigadier")
            if isinstance(name_candidate, str) and name_candidate.strip():
                brigadier_name = name_candidate.strip()
        if not brigadier_name and profile is not None:
            brigadier_name = profile.fio or profile.fio_compact
        if not brigadier_name:
            brigadier_name = (
                message.from_user.full_name
                or message.from_user.username
                or str(user_id)
            )
        report_text = build_group_report(brigadier_name, summary)
        try:
            await message.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=report_text,
            )
            group_sent = True
        except Exception:  # noqa: BLE001
            logger.exception(
                "Не удалось отправить отчёт в групповой чат %s", GROUP_CHAT_ID
            )

    confirmation = (
        "смена закрыта. отчёт отправлен в групповой чат."
        if group_sent
        else "смена закрыта."
    )
    await message.answer(confirmation + "\nвозвращаю в главное меню…")

    from features.main_menu import show_menu

    await show_menu(message, service=sheets)
