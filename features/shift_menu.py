"""Меню оформления смены и статусы разделов."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from features.utils.locks import acquire_user_lock, release_user_lock
from features.utils.messaging import safe_delete, send_progress
from services.sheets import SheetsService
from services.env import group_notifications_enabled

router = Router()
_service: SheetsService | None = None
logger = logging.getLogger(__name__)

GROUP_CHAT_ID = -1003298300145


class ShiftState(StatesGroup):
    """Глобальные состояния процесса оформления смены."""

    IDLE = State()
    ACTIVE = State()


class Mode(StatesGroup):
    """Состояния перехода в подрежимы."""

    EXPENSES = State()
    MATERIALS = State()
    CREW = State()


@dataclass
class ShiftSession:
    """Отражает локальный прогресс заполнения смены."""

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
BTN_FINISH_SHIFT = "✅ Завершить смену"

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


def reset_shift_session(user_id: int) -> None:
    """Очищает кеш сессии пользователя."""

    _sessions.pop(user_id, None)


def _sync_session(
    user_id: int,
    *,
    row: int,
    progress: dict[str, bool],
    shift_date: str,
    closed: bool,
) -> ShiftSession:
    """Обновляет кеш прогресса пользователя и возвращает его."""

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
    """Помечает раздел заполненным в локальной сессии."""

    session = _sessions.get(user_id)
    key = MODE_KEYS.get(mode)
    if session and key:
        session.modes[key] = True


def mark_shift_closed(user_id: int) -> None:
    """Помечает смену как закрытую в локальной сессии."""

    session = _sessions.get(user_id)
    if session:
        session.closed = True


def _payload(action: str, **extra: Any) -> str:
    """Собирает JSON-пейлоад для inline-кнопок."""

    data = {"a": action}
    data.update(extra)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _keyboard(session: ShiftSession) -> types.InlineKeyboardMarkup:
    """Собирает inline-клавиатуру меню смены."""

    builder = InlineKeyboardBuilder()
    builder.button(
        text=BTN_EXPENSES_LABEL,
        callback_data=_payload("open_mode", m="expenses"),
    )
    builder.button(
        text=BTN_MATERIALS_LABEL,
        callback_data=_payload("open_mode", m="materials"),
    )
    builder.button(
        text=BTN_CREW_LABEL,
        callback_data=_payload("open_mode", m="crew"),
    )
    if all(session.modes.values()) and not session.closed:
        builder.button(
            text=BTN_FINISH_SHIFT,
            callback_data=_payload("finish_shift"),
        )
    builder.button(
        text="⬅ В главное меню",
        callback_data=_payload("shift_menu", m="home"),
    )
    builder.adjust(1)
    return builder.as_markup()


async def render_shift_menu(
    message: types.Message,
    user_id: int,
    row: int | None,
    service: SheetsService | None = None,
    *,
    state: FSMContext | None = None,
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

    try:
        shift_date_raw = await asyncio.to_thread(
            sheets.get_shift_date, row_index
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось получить дату смены (user_id=%s, row=%s)", user_id, row_index
        )
        shift_date_raw = ""

    shift_date = (shift_date_raw or date.today().isoformat()).strip()
    session = _sync_session(
        user_id,
        row=row_index,
        progress=progress,
        shift_date=shift_date,
        closed=shift_closed,
    )

    if state is not None:
        await state.set_state(ShiftState.ACTIVE)

    lines = [
        "🗂 Меню оформления смены",
        f"Дата: {_format_date_for_summary(session.date)}",
        "",
        _line(BTN_EXPENSES_LABEL, session.modes["expenses"]),
        _line(BTN_MATERIALS_LABEL, session.modes["materials"]),
        _line(BTN_CREW_LABEL, session.modes["crew"]),
    ]

    if session.closed:
        lines.extend(
            [
                "",
                "Смена уже закрыта. Вернитесь в главное меню, чтобы открыть новую смену завтра.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Выберите раздел для заполнения. Кнопка «Завершить смену» появится, когда все разделы будут отмечены как готовые.",
            ]
        )

    await message.answer("\n".join(lines), reply_markup=_keyboard(session))


def _parse_payload(raw: str | None) -> dict[str, Any] | None:
    """Безопасно разбирает JSON-пейлоад из callback_data."""

    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Некорректный payload в callback: %s", raw)
        return None
    if not isinstance(data, dict):
        return None
    return data


async def _open_mode(callback: types.CallbackQuery, state: FSMContext, mode: str) -> None:
    """Запускает подрежим оформления смены."""

    handlers = {
        "expenses": (
            Mode.EXPENSES,
            "start_expenses",
            "features.expenses",
        ),
        "materials": (
            Mode.MATERIALS,
            "start_materials",
            "features.materials",
        ),
        "crew": (
            Mode.CREW,
            "start_crew",
            "features.crew",
        ),
    }

    target = handlers.get(mode)
    if not target:
        await callback.answer("Раздел недоступен", show_alert=True)
        return

    await state.update_data(_shift_user_id=callback.from_user.id)
    await state.set_state(target[0])
    module_name = target[2]
    function_name = target[1]
    module = __import__(module_name, fromlist=[function_name])
    handler = getattr(module, function_name)
    await callback.answer()
    await handler(callback.message, state, user_id=callback.from_user.id)


async def _refresh_menu(
    callback: types.CallbackQuery,
    state: FSMContext,
    payload: dict[str, Any],
) -> None:
    """Перерисовывает меню или возвращает пользователя в главное меню."""

    destination = payload.get("m")
    if destination == "home":
        from features.main_menu import show_menu

        await callback.answer()
        await safe_delete(callback.message)
        await show_menu(callback.message, state=state)
        return

    session = _sessions.get(callback.from_user.id)
    row = session.row if session else None
    await callback.answer()
    await render_shift_menu(
        callback.message,
        callback.from_user.id,
        row,
        state=state,
        delete_trigger_message=True,
        show_progress=False,
    )


async def _mark_mode_done_from_callback(
    callback: types.CallbackQuery, mode: str, state: FSMContext
) -> None:
    """Обновляет прогресс и возвращает пользователя в меню."""

    mark_mode_done(callback.from_user.id, mode)
    session = _sessions.get(callback.from_user.id)
    row = session.row if session else None
    await callback.answer("Раздел отмечен как заполненный")
    await render_shift_menu(
        callback.message,
        callback.from_user.id,
        row,
        state=state,
        delete_trigger_message=True,
        show_progress=False,
    )


@router.callback_query()
async def handle_shift_callback(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Единая точка обработки callback-кнопок меню смены."""

    payload = _parse_payload(callback.data)
    if not payload:
        return

    action = payload.get("a")

    if not action:
        await callback.answer("Неизвестная команда", show_alert=True)
        return

    if action == "shift_menu":
        await _refresh_menu(callback, state, payload)
        return

    if action == "open_mode":
        await _open_mode(callback, state, str(payload.get("m")))
        return

    if action == "finish_shift":
        await close_shift(callback, state)
        return

    if action in {"expenses_done", "materials_done", "crew_done"}:
        mode = action.split("_")[0]
        await _mark_mode_done_from_callback(callback, mode, state)
        return

    await callback.answer("Команда не поддерживается", show_alert=True)


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


async def close_shift(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает закрытие смены и отправку сводки."""

    await callback.answer()

    user_id = callback.from_user.id
    message = callback.message
    sheets = _resolve_service(None)

    await safe_delete(message)

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
                disable_web_page_preview=True,
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

    mark_shift_closed(user_id)
    await state.set_state(ShiftState.IDLE)

    from features.main_menu import show_menu

    await show_menu(message, service=sheets, state=state)
