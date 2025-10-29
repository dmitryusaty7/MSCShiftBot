"""Меню смены на reply-кнопках и логика переходов между разделами."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.dashboard import (
    SHIFT_BACK_BUTTON,
    FINISH_SHIFT_BUTTON,
    shift_menu_keyboard,
)
from bot.utils.cleanup import send_screen_message
from bot.utils.flash import flash_message
from features.utils.locks import acquire_user_lock, release_user_lock
from features.utils.messaging import safe_delete
from services.env import group_notifications_enabled
from services.sheets import SheetsService

router = Router(name="shift-menu")

logger = logging.getLogger(__name__)
_service: SheetsService | None = None

GROUP_CHAT_ID = -1003298300145


class ShiftState(StatesGroup):
    """Глобальные состояния процесса оформления смены."""

    IDLE = State()
    ACTIVE = State()


class Mode(StatesGroup):
    """Состояния перехода в подрежимы смены."""

    EXPENSES = State()
    MATERIALS = State()
    CREW = State()


@dataclass
class ShiftSession:
    """Кеш локального прогресса пользователя по смене."""

    date: str
    row: int
    modes: dict[str, bool]
    closed: bool = False


_sessions: dict[int, ShiftSession] = {}

MODE_KEYS = {
    "expenses": "expenses",
    "materials": "materials",
    "crew": "crew",
}


def _get_service() -> SheetsService:
    """Возвращает (или создаёт) общий экземпляр SheetsService."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


def _resolve_service(service: SheetsService | None) -> SheetsService:
    """Возвращает переданный сервис или общий экземпляр."""

    global _service
    if service is not None:
        _service = service
        return service
    return _get_service()


def status_badge(done: bool) -> str:
    """Формирует подпись статуса раздела."""

    return "✅ готово" if done else "✍️ заполнить"


def _line(label: str, done: bool) -> str:
    """Возвращает строку статуса для текста меню."""

    return f"{label} — {status_badge(done)}"


def reset_shift_session(user_id: int) -> None:
    """Очищает кеш прогресса пользователя."""

    _sessions.pop(user_id, None)


def _sync_session(
    user_id: int,
    *,
    row: int,
    progress: dict[str, bool],
    shift_date: str,
    closed: bool,
) -> ShiftSession:
    """Синхронизирует прогресс пользователя и возвращает объект сессии."""

    session = _sessions.get(user_id)
    if session is None or session.date != shift_date:
        session = ShiftSession(
            date=shift_date,
            row=row,
            modes={key: bool(progress.get(key, False)) for key in MODE_KEYS.values()},
            closed=closed,
        )
        _sessions[user_id] = session
    else:
        session.row = row
        session.closed = closed
        for key in MODE_KEYS.values():
            if key in progress:
                session.modes[key] = bool(progress[key])
    return session


def mark_mode_done(user_id: int, mode: str) -> None:
    """Помечает раздел заполненным в локальном кеше пользователя."""

    session = _sessions.get(user_id)
    key = MODE_KEYS.get(mode)
    if session and key:
        session.modes[key] = True


def mark_shift_closed(user_id: int) -> None:
    """Помечает смену как закрытую в локальном кеше."""

    session = _sessions.get(user_id)
    if session:
        session.closed = True


def _format_date_for_summary(date_value: str) -> str:
    """Приводит дату к формату ДД.ММ.ГГГГ для отображения."""

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
    """Формирует текст отчёта для группового чата по итогам смены."""

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
    total_text = f"{total_amount:,}".replace(",", " ")

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


async def _load_shift_summary(service: SheetsService, row: int) -> dict[str, Any]:
    """Читает сводку смены из таблицы."""

    summary = await asyncio.to_thread(service.get_shift_summary, row)
    if not isinstance(summary, dict):
        raise RuntimeError("не удалось получить сводку смены")
    return summary


def _menu_lines(session: ShiftSession) -> list[str]:
    """Формирует текст меню смены с текущими статусами."""

    lines = [
        "🗂 Меню оформления смены",
        f"Дата: {_format_date_for_summary(session.date)}",
        "",
        _line("🧾 Расходы", session.modes["expenses"]),
        _line("📦 Материалы", session.modes["materials"]),
        _line("👥 Состав бригады", session.modes["crew"]),
        "",
        "Выберите раздел для заполнения. Кнопка «Завершить смену» появится, когда все разделы будут отмечены как готовые.",
    ]
    if session.closed:
        lines.append(
            "Смена уже закрыта. Вернитесь в главную панель, чтобы открыть новую смену завтра."
        )
    return lines


async def render_shift_menu(
    message: types.Message,
    user_id: int,
    row: int | None,
    service: SheetsService | None = None,
    *,
    state: FSMContext | None = None,
    delete_trigger_message: bool = False,
    show_loading: bool = False,
    show_progress: bool = True,
    use_screen_message: bool = False,
) -> None:
    """Отображает меню смены с учётом прогресса заполнения."""

    sheets = _resolve_service(service)

    if delete_trigger_message:
        await safe_delete(message)

    loading_flash = None
    if show_loading:
        try:
            loading_flash = await flash_message(
                message, "Загружаю…", ttl=2.0
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось отправить flash перед меню смены (user_id=%s)",
                user_id,
                exc_info=True,
            )

    loader = None
    if show_progress:
        try:
            loader = await flash_message(
                message, "⏳ Проверяю статус смены. Подождите…", ttl=2.0
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось отправить сообщение о проверке прогресса (user_id=%s)",
                user_id,
                exc_info=True,
            )

    target_row = row
    lock = None
    try:
        if target_row is None:
            lock = await acquire_user_lock(user_id)
            if lock is None:
                await message.answer(
                    "Предыдущее действие ещё выполняется. Повторите попытку через несколько секунд."
                )
                return
            target_row = await asyncio.to_thread(sheets.open_shift_for_user, user_id)

        progress = await asyncio.to_thread(
            sheets.get_shift_progress, user_id, target_row
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось получить прогресс смены (user_id=%s, row=%s)", user_id, target_row
        )
        await message.answer(
            "Не удалось открыть меню смены. Попробуйте позже или обратитесь к координатору."
        )
        return
    finally:
        if lock is not None:
            release_user_lock(lock)
        await safe_delete(loader)
        await safe_delete(loading_flash)

    if target_row is None:
        await message.answer(
            "Не удалось подготовить смену. Попробуйте позже или обратитесь к координатору."
        )
        return

    try:
        shift_closed = await asyncio.to_thread(sheets.is_shift_closed, target_row)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось проверить статус закрытия смены (user_id=%s, row=%s)",
            user_id,
            target_row,
        )
        shift_closed = False

    try:
        raw_date = await asyncio.to_thread(sheets.get_shift_date, target_row)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось получить дату смены (user_id=%s, row=%s)", user_id, target_row
        )
        raw_date = date.today().isoformat()

    session = _sync_session(
        user_id,
        row=target_row,
        progress=progress,
        shift_date=raw_date or date.today().isoformat(),
        closed=shift_closed,
    )

    if state is not None:
        await state.set_state(ShiftState.ACTIVE)

    bot = message.bot
    chat_id = message.chat.id

    previous_menu_id = None
    if state is not None:
        data = await state.get_data()
        previous_menu_id = data.get("shift_menu_message_id")
        if previous_menu_id:
            try:
                await bot.delete_message(chat_id, previous_menu_id)
            except TelegramBadRequest:
                pass

    markup = shift_menu_keyboard(
        expenses_done=session.modes["expenses"],
        materials_done=session.modes["materials"],
        crew_done=session.modes["crew"],
        show_finish=all(session.modes.values()) and not session.closed,
    )

    lines = "\n".join(_menu_lines(session))
    if use_screen_message:
        menu_message = await send_screen_message(
            message,
            lines,
            reply_markup=markup,
        )
    else:
        menu_message = await message.answer(lines, reply_markup=markup)

    if state is not None:
        await state.update_data(
            shift_menu_message_id=menu_message.message_id,
            dashboard_message_id=None,
        )


async def _prepare_mode(
    message: types.Message,
    state: FSMContext,
    *,
    mode: Mode,
    module_path: str,
    func_name: str,
) -> None:
    """Запускает подрежим оформления смены."""

    await flash_message(message, "Загружаю…", ttl=2.0)
    user_id = message.from_user.id
    await state.update_data(_shift_user_id=user_id)
    await state.set_state(mode)
    module = __import__(module_path, fromlist=[func_name])
    handler = getattr(module, func_name)
    await handler(message, state, user_id=user_id)


@router.message(ShiftState.ACTIVE, F.text.startswith("🧾 Расходы"))
async def handle_expenses(message: types.Message, state: FSMContext) -> None:
    """Открывает раздел «Расходы» из меню смены."""

    await _prepare_mode(
        message,
        state,
        mode=Mode.EXPENSES,
        module_path="features.expenses",
        func_name="start_expenses",
    )


@router.message(ShiftState.ACTIVE, F.text.startswith("📦 Материалы"))
async def handle_materials(message: types.Message, state: FSMContext) -> None:
    """Открывает раздел «Материалы» из меню смены."""

    await _prepare_mode(
        message,
        state,
        mode=Mode.MATERIALS,
        module_path="features.materials",
        func_name="start_materials",
    )


@router.message(ShiftState.ACTIVE, F.text.startswith("👥 Состав бригады"))
async def handle_crew(message: types.Message, state: FSMContext) -> None:
    """Открывает раздел «Состав бригады» из меню смены."""

    await _prepare_mode(
        message,
        state,
        mode=Mode.CREW,
        module_path="features.crew",
        func_name="start_crew",
    )


async def _ensure_session(user_id: int) -> ShiftSession | None:
    """Возвращает актуальную сессию пользователя, если она есть."""

    session = _sessions.get(user_id)
    return session


@router.message(ShiftState.ACTIVE, F.text == SHIFT_BACK_BUTTON)
async def handle_back_to_dashboard(message: types.Message, state: FSMContext) -> None:
    """Возвращает пользователя в главную панель."""

    from bot.handlers.dashboard import show_dashboard

    await flash_message(message, "Загружаю…", ttl=2.0)
    await show_dashboard(message, state=state)


@router.message(ShiftState.ACTIVE, F.text == FINISH_SHIFT_BUTTON)
async def handle_finish_shift(message: types.Message, state: FSMContext) -> None:
    """Проверяет заполненность разделов и закрывает смену."""

    session = await _ensure_session(message.from_user.id)
    if session is None:
        await message.answer(
            "Не удалось определить рабочую строку. Попробуйте позже или обратитесь к координатору."
        )
        return

    await flash_message(message, "Проверяю данные…", ttl=2.0)
    await _close_shift(message, state, session)


async def _close_shift(
    message: types.Message,
    state: FSMContext,
    session: ShiftSession,
) -> None:
    """Выполняет закрытие смены и возвращает пользователя в главную панель."""

    user_id = message.from_user.id
    sheets = _resolve_service(None)

    row = session.row
    if not row:
        await message.answer(
            "Рабочая строка не найдена. Начните смену заново через главную панель."
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
            "Не удалось проверить состояние смены. Попробуйте позже."
        )
        return

    if already_closed:
        await message.answer("Смена уже закрыта.")
        return

    try:
        progress = await asyncio.to_thread(sheets.get_shift_progress, user_id, row)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось получить прогресс смены перед закрытием (user_id=%s, row=%s)",
            user_id,
            row,
        )
        await message.answer(
            "Не удалось проверить заполненность разделов. Попробуйте позже."
        )
        return

    if not all(progress.values()):
        await message.answer(
            "Не все разделы заполнены. Заполните разделы и попробуйте снова."
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
        summary = await _load_shift_summary(sheets, row)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось собрать сводку смены (user_id=%s, row=%s)", user_id, row
        )
        await message.answer(
            "Не удалось сформировать сводку. Попробуйте позже."
        )
        return

    try:
        closed_now = await asyncio.to_thread(sheets.finalize_shift, user_id, row)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка при закрытии смены (user_id=%s, row=%s)", user_id, row)
        await message.answer(
            "Не удалось закрыть смену. Попробуйте позже или обратитесь к координатору."
        )
        return

    if not closed_now:
        await message.answer("Смена уже закрыта.")
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
                disable_web_page_preview=True,
            )
            group_sent = True
        except Exception:  # noqa: BLE001
            logger.exception(
                "Не удалось отправить отчёт в групповой чат %s", GROUP_CHAT_ID
            )

    confirmation = (
        "Смена закрыта. Отчёт отправлен в групповой чат."
        if group_sent
        else "Смена закрыта."
    )
    await message.answer(confirmation + "\nВозвращаю в главную панель…")

    mark_shift_closed(user_id)
    if state is not None:
        await state.set_state(ShiftState.IDLE)

    from bot.handlers.dashboard import show_dashboard

    await show_dashboard(message, service=sheets, state=state)
