"""Прямая логика закрытия смены из меню с подтверждением и уведомлением."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Any

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.handlers.shift_menu import (
    ShiftState,
    get_shift_session,
    mark_shift_closed,
    render_shift_menu,
)
from bot.keyboards.dashboard import FINISH_SHIFT_BUTTON
from bot.keyboards.shift_close import (
    CANCEL_CLOSE_BUTTON,
    CONFIRM_CLOSE_BUTTON,
    close_confirmation_keyboard,
)
from bot.utils.cleanup import cleanup_after_confirm, cleanup_screen, send_screen_message
from bot.utils.flash import flash_message
from services.env import get_group_chat_id
from services.sheets import SheetsService

logger = logging.getLogger(__name__)

router = Router(name="shift-close")

_service: SheetsService | None = None
_NOTIFY_CACHE_TTL = timedelta(minutes=10)
_last_notified: dict[int, datetime] = {}


class ShiftCloseState(StatesGroup):
    """Состояния подтверждения закрытия смены."""

    CONFIRM = State()


@dataclass
class GroupNotificationContext:
    """Контекст данных для группового уведомления."""

    date: str
    user: str
    vessel: str
    statuses: str
    expenses_total: str
    materials_summary: str
    crew_summary: str


def _get_service() -> SheetsService:
    """Возвращает общий экземпляр сервиса таблиц."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


def _format_money(value: int) -> str:
    """Форматирует сумму расходов с разделением тысяч и значком рубля."""

    formatted = f"{value:,}".replace(",", " ")
    return f"{formatted} ₽"


def _format_materials(summary: dict[str, Any] | None) -> str:
    """Формирует краткое описание материалов."""

    if not isinstance(summary, dict):
        return "—"

    pvd = summary.get("pvd_rolls_m")
    pvc = summary.get("pvc_tubes")
    tape = summary.get("tape")
    photos = summary.get("photos_link")

    parts: list[str] = []
    if isinstance(pvd, int) and pvd > 0:
        parts.append(f"ПВД {pvd} м")
    if isinstance(pvc, int) and pvc > 0:
        parts.append(f"ПВХ {pvc} шт")
    if isinstance(tape, int) and tape > 0:
        parts.append(f"Скотч {tape} шт")
    if isinstance(photos, str) and photos.strip():
        parts.append("Фото загружены")

    return "; ".join(parts) if parts else "—"


def _format_crew(summary: dict[str, Any] | None) -> str:
    """Собирает список участников бригады."""

    if not isinstance(summary, dict):
        return "—"

    crew_members: list[str] = []
    driver = summary.get("driver")
    if isinstance(driver, str) and driver.strip():
        crew_members.append(driver.strip())
    workers = summary.get("workers")
    if isinstance(workers, list):
        crew_members.extend(member.strip() for member in workers if isinstance(member, str) and member.strip())

    return ", ".join(crew_members) if crew_members else "—"


def _parse_int(value: Any) -> int:
    """Безопасно преобразует значение к целому числу."""

    if isinstance(value, bool):  # pragma: no cover - защитный сценарий
        return int(value)
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError):  # pragma: no cover - защитное ветвление
            return 0
    if isinstance(value, str):
        stripped = value.strip().replace("\u202f", "").replace(" ", "")
        if not stripped:
            return 0
        try:
            return int(float(stripped)) if "." in stripped else int(stripped)
        except ValueError:
            digits = "".join(ch for ch in stripped if ch.isdigit() or ch == "-")
            return int(digits) if digits else 0
    return 0


def _compose_notification_context(
    user_name: str,
    summary: dict[str, Any],
) -> GroupNotificationContext:
    """Строит контекст уведомления для группового чата."""

    expenses = summary.get("expenses", {}) if isinstance(summary, dict) else {}
    materials = summary.get("materials") if isinstance(summary, dict) else None
    crew = summary.get("crew") if isinstance(summary, dict) else None

    total = 0
    if isinstance(expenses, dict):
        provided_total = _parse_int(expenses.get("total"))
        calculated = sum(
            _parse_int(expenses.get(key))
            for key in ("driver", "brigadier", "workers", "aux", "food", "taxi", "other")
        )
        total = provided_total or calculated

    date_value = str(summary.get("date", "")).strip() or "—"
    vessel_value = str(summary.get("ship", "")).strip() or "—"

    statuses_line = "📊 Статусы: ✅ Расходы • ✅ Материалы • ✅ Бригада"

    return GroupNotificationContext(
        date=date_value,
        user=user_name,
        vessel=vessel_value,
        statuses=statuses_line,
        expenses_total=_format_money(total),
        materials_summary=_format_materials(materials),
        crew_summary=_format_crew(crew),
    )


def _format_group_report(ctx: GroupNotificationContext) -> str:
    """Формирует HTML-сообщение для рабочего чата."""

    return (
        "<b>✅ Смена закрыта</b>\n"
        f"📅 {escape(ctx.date)}\n"
        f"🧑‍✈️ {escape(ctx.user)}\n"
        f"🛥 {escape(ctx.vessel)}\n"
        f"{escape(ctx.statuses)}\n\n"
        f"🧾 Расходы: {escape(ctx.expenses_total)}\n"
        f"📦 Материалы: {escape(ctx.materials_summary)}\n"
        f"👥 Бригада: {escape(ctx.crew_summary)}"
    )


def _prune_cache(now: datetime) -> None:
    """Удаляет устаревшие записи о доставленных уведомлениях."""

    stale = [row for row, timestamp in _last_notified.items() if now - timestamp >= _NOTIFY_CACHE_TTL]
    for row in stale:
        _last_notified.pop(row, None)


def _should_skip_notification(row: int, now: datetime) -> bool:
    """Проверяет, отправлялось ли уведомление по строке недавно."""

    _prune_cache(now)
    last = _last_notified.get(row)
    return bool(last and now - last < _NOTIFY_CACHE_TTL)


def _mark_notified(row: int, now: datetime) -> None:
    """Запоминает факт отправки уведомления для строки смены."""

    _last_notified[row] = now


async def _notify_group(bot: types.Bot, ctx: GroupNotificationContext, *, row: int) -> None:
    """Отправляет уведомление в рабочую группу, если она настроена."""

    now = datetime.now()
    if _should_skip_notification(row, now):
        logger.info("Пропуск уведомления для строки %s: отправлено ранее", row)
        return

    try:
        chat_id = get_group_chat_id()
    except RuntimeError as exc:  # неправильное значение в окружении
        logger.warning("GROUP_CHAT_ID некорректен: %s", exc)
        _mark_notified(row, now)
        return

    if not chat_id:
        logger.warning("GROUP_CHAT_ID не указан, уведомление пропущено")
        _mark_notified(row, now)
        return

    text = _format_group_report(ctx)
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
        logger.info("Групповое уведомление отправлено: chat=%s, length=%s", chat_id, len(text))
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Не удалось отправить уведомление в чат %s: %s", chat_id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Сбой при отправке уведомления в чат %s: %s", chat_id, exc)
    finally:
        _mark_notified(row, now)


async def _return_to_menu(
    message: types.Message,
    state: FSMContext,
    *,
    user_id: int,
    row: int | None,
    service: SheetsService,
    flash_text: str | None = None,
    flash_ttl: float = 3.0,
) -> None:
    """Возвращает пользователя в меню смены после ошибки или отмены."""

    await cleanup_screen(message.bot, message.chat.id, keep_start=True)
    if flash_text:
        await flash_message(message, flash_text, ttl=flash_ttl)
    await render_shift_menu(
        message,
        user_id,
        row,
        service=service,
        state=state,
        delete_trigger_message=False,
        show_progress=False,
        use_screen_message=True,
    )


@router.message(ShiftState.ACTIVE, F.text == FINISH_SHIFT_BUTTON)
async def handle_shift_close_request(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает запрос на закрытие смены из меню."""

    user_id = message.from_user.id
    session = get_shift_session(user_id)
    if session is None or not getattr(session, "row", None):
        await message.answer(
            "Не удалось определить активную смену. Попробуйте позже или обратитесь к координатору."
        )
        return

    service = _get_service()
    row = session.row

    try:
        await flash_message(message, "💾 Проверяю готовность разделов…", ttl=1.5)
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось показать flash перед проверкой закрытия", exc_info=True)

    try:
        closed = await asyncio.to_thread(service.is_shift_closed, row)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка проверки статуса закрытия (user_id=%s, row=%s)", user_id, row)
        await message.answer(
            "⚠️ Не удалось проверить статус смены. Попробуйте позже."  # noqa: G004
        )
        return

    if closed:
        mark_shift_closed(user_id)
        await message.answer("Смена уже закрыта. Откройте новую смену через главную панель завтра.")
        return

    try:
        progress = await asyncio.to_thread(service.get_shift_progress, user_id, row)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось получить прогресс смены (user_id=%s, row=%s)", user_id, row)
        await message.answer(
            "⚠️ Ошибка при проверке разделов. Попробуйте позже."  # noqa: G004
        )
        return

    if not all(progress.values()):
        await flash_message(
            message,
            "⚠️ Не все разделы заполнены. Проверьте Расходы, Материалы и Состав.",
            ttl=3.0,
        )
        await render_shift_menu(
            message,
            user_id,
            row,
            service=service,
            state=state,
            delete_trigger_message=False,
            show_progress=False,
            use_screen_message=True,
        )
        return

    await state.update_data(shift_close_row=row)
    await state.set_state(ShiftCloseState.CONFIRM)

    await cleanup_screen(message.bot, message.chat.id, keep_start=True)
    await send_screen_message(
        message,
        "✅ Все разделы заполнены.\n\nЗакрыть смену и зафиксировать данные?",
        reply_markup=close_confirmation_keyboard(),
    )


async def _resolve_brigadier_name(
    user_id: int,
    summary: dict[str, Any] | None,
    *,
    service: SheetsService,
    message: types.Message,
) -> str:
    """Определяет ФИО бригадира для отчёта."""

    brigadier = ""
    if isinstance(summary, dict):
        crew = summary.get("crew")
        if isinstance(crew, dict):
            candidate = crew.get("brigadier")
            if isinstance(candidate, str) and candidate.strip():
                brigadier = candidate.strip()
    if brigadier:
        return brigadier

    profile = None
    try:
        profile = await asyncio.to_thread(service.get_user_profile, user_id)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось получить профиль пользователя %s", user_id, exc_info=True)

    if profile and getattr(profile, "fio", ""):
        return profile.fio
    if profile and getattr(profile, "fio_compact", ""):
        return profile.fio_compact

    full_name = message.from_user.full_name if message.from_user else ""
    if full_name:
        return full_name
    username = message.from_user.username if message.from_user else ""
    if username:
        return f"@{username}" if not username.startswith("@") else username
    return str(user_id)


@router.message(ShiftCloseState.CONFIRM, F.text == CONFIRM_CLOSE_BUTTON)
async def handle_shift_close_confirm(message: types.Message, state: FSMContext) -> None:
    """Подтверждает закрытие смены после проверки пользователя."""

    user_id = message.from_user.id
    data = await state.get_data()
    row = data.get("shift_close_row") if isinstance(data, dict) else None
    if not isinstance(row, int) or row <= 0:
        await state.set_state(ShiftState.ACTIVE)
        await render_shift_menu(
            message,
            user_id,
            None,
            service=_get_service(),
            state=state,
            delete_trigger_message=False,
            show_progress=True,
            use_screen_message=True,
        )
        return

    service = _get_service()

    try:
        await flash_message(message, "💾 Сохраняю…", ttl=1.5)
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось показать flash при сохранении", exc_info=True)

    summary: dict[str, Any] | None = None
    try:
        summary = await asyncio.to_thread(service.get_shift_summary, row)
        if not isinstance(summary, dict):
            raise RuntimeError("summary must be dict")
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось получить сводку смены (user_id=%s, row=%s)", user_id, row)
        await state.set_state(ShiftState.ACTIVE)
        await state.update_data(shift_close_row=None)
        await _return_to_menu(
            message,
            state,
            user_id=user_id,
            row=row,
            service=service,
            flash_text="⚠️ Ошибка при закрытии смены. Попробуйте позже.",
        )
        return

    try:
        closed_now = await asyncio.to_thread(service.finalize_shift, user_id, row)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка при закрытии смены (user_id=%s, row=%s)", user_id, row)
        await state.set_state(ShiftState.ACTIVE)
        await state.update_data(shift_close_row=None)
        await _return_to_menu(
            message,
            state,
            user_id=user_id,
            row=row,
            service=service,
            flash_text="⚠️ Ошибка при закрытии смены. Попробуйте позже.",
        )
        return

    if not closed_now:
        logger.info("Смена уже была закрыта ранее (user_id=%s, row=%s)", user_id, row)

    now = datetime.now()
    brigadier_name = await _resolve_brigadier_name(
        user_id,
        summary,
        service=service,
        message=message,
    )
    context = _compose_notification_context(brigadier_name, summary)
    await _notify_group(message.bot, context, row=row)

    mark_shift_closed(user_id)
    await state.set_state(ShiftState.IDLE)
    await state.update_data(shift_close_row=None)

    await cleanup_after_confirm(message, state, keep_start=False)

    try:
        await flash_message(message, "✅ Смена закрыта.", ttl=1.5)
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось показать подтверждение закрытия", exc_info=True)

    from bot.handlers.dashboard import show_dashboard

    await show_dashboard(message, state=state, service=service)


@router.message(ShiftCloseState.CONFIRM, F.text == CANCEL_CLOSE_BUTTON)
async def handle_shift_close_cancel(message: types.Message, state: FSMContext) -> None:
    """Отменяет процедуру закрытия и возвращает пользователя в меню."""

    user_id = message.from_user.id
    data = await state.get_data()
    row = data.get("shift_close_row") if isinstance(data, dict) else None
    service = _get_service()

    await state.set_state(ShiftState.ACTIVE)
    await state.update_data(shift_close_row=None)

    await cleanup_screen(message.bot, message.chat.id, keep_start=True)
    await flash_message(message, "↩ Закрытие отменено.", ttl=1.2)
    await render_shift_menu(
        message,
        user_id,
        row,
        service=service,
        state=state,
        delete_trigger_message=False,
        show_progress=False,
        use_screen_message=True,
    )
