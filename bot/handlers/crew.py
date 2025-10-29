"""Новый сценарий раздела «Бригада» с мгновенным списком рабочих."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.crew_inline import REMOVE_PREFIX, make_list_kb
from bot.keyboards.crew_reply import (
    ADD_WORKER_BUTTON,
    BACK_BUTTON,
    CLEAR_WORKERS_BUTTON,
    CONFIRM_BUTTON,
    EDIT_BUTTON,
    crew_confirm_keyboard,
    crew_start_keyboard,
)
from bot.keyboards.dashboard import SHIFT_BACK_BUTTON
from bot.services import CrewSheetsService, CrewWorker
from bot.utils.cleanup import cleanup_screen, remember_message, send_screen_message
from bot.utils.flash import flash_message
from features.utils.messaging import safe_delete

router = Router(name="crew")
logger = logging.getLogger(__name__)

_service: CrewSheetsService | None = None


class CrewState(StatesGroup):
    """Состояния раздела «Бригада» в новом боте."""

    WORKERS = State()
    AWAIT_WORKER = State()
    CONFIRM = State()


# Работа с контекстом -----------------------------------------------------

def _make_tracker() -> Dict[str, Any]:
    return {"prompt_id": None, "user_messages": [], "bot_messages": []}


async def _get_context(state: FSMContext) -> Dict[str, Any]:
    data = await state.get_data()
    context = data.get("crew_ctx")
    if not isinstance(context, dict):
        context = {}
    tracker = context.get("tracker")
    if not isinstance(tracker, dict):
        tracker = _make_tracker()
    else:
        tracker.setdefault("prompt_id", None)
        tracker.setdefault("user_messages", [])
        tracker.setdefault("bot_messages", [])
    context["tracker"] = tracker
    return context


async def _save_context(state: FSMContext, context: Dict[str, Any]) -> None:
    await state.update_data(crew_ctx=context)


async def _set_prompt(message: types.Message, state: FSMContext, *, prompt: types.Message) -> None:
    context = await _get_context(state)
    tracker = _make_tracker()
    tracker["prompt_id"] = prompt.message_id
    context["tracker"] = tracker
    await _save_context(state, context)
    remember_message(message.chat.id, prompt.message_id)


async def _add_user_message(state: FSMContext, message_id: int, chat_id: int) -> None:
    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    tracker.setdefault("user_messages", []).append(message_id)
    context["tracker"] = tracker
    await _save_context(state, context)
    remember_message(chat_id, message_id)


async def _add_bot_message(state: FSMContext, message_id: int, chat_id: int) -> None:
    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    tracker.setdefault("bot_messages", []).append(message_id)
    context["tracker"] = tracker
    await _save_context(state, context)
    remember_message(chat_id, message_id)


async def _cleanup_step(message: types.Message, state: FSMContext) -> None:
    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    chat_id = message.chat.id
    bot = message.bot
    prompt_id = tracker.get("prompt_id")
    bot_messages = tracker.get("bot_messages", [])
    user_messages = tracker.get("user_messages", [])
    ids: List[int] = []
    if prompt_id:
        ids.append(prompt_id)
    ids.extend(bot_messages)
    for message_id in ids:
        if not message_id:
            continue
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            logger.debug("Сообщение %s уже удалено", message_id)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось удалить сообщение %s", message_id, exc_info=True)
    for message_id in user_messages:
        if not message_id:
            continue
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            logger.debug("Сообщение пользователя %s уже удалено", message_id)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось удалить сообщение пользователя %s", message_id, exc_info=True)
    context["tracker"] = _make_tracker()
    await _save_context(state, context)


# Служебные функции --------------------------------------------------------

def _get_service() -> CrewSheetsService:
    global _service
    if _service is None:
        _service = CrewSheetsService()
    return _service


def _serialize_worker(worker: CrewWorker) -> Dict[str, Any]:
    return {"id": worker.worker_id, "name": worker.name}


def _deserialize_worker(raw: Any) -> CrewWorker | None:
    if not isinstance(raw, dict):
        return None
    worker_id = raw.get("id")
    name = raw.get("name")
    if not isinstance(worker_id, int) or not isinstance(name, str):
        return None
    return CrewWorker(worker_id=worker_id, name=name)


def _deserialize_workers(items: Any) -> list[CrewWorker]:
    if not isinstance(items, list):
        return []
    result: list[CrewWorker] = []
    for raw in items:
        worker = _deserialize_worker(raw)
        if worker is not None:
            result.append(worker)
    return result


def _worker_map(context: Dict[str, Any]) -> dict[int, CrewWorker]:
    workers_raw = context.get("workers", [])
    workers = _deserialize_workers(workers_raw)
    return {worker.worker_id: worker for worker in workers}


async def _update_screen(
    message: types.Message,
    state: FSMContext,
    *,
    text: str,
    reply_markup: Any,
) -> None:
    context = await _get_context(state)
    screen_id = context.get("screen_message_id")
    if isinstance(screen_id, int) and screen_id > 0:
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=screen_id,
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            logger.debug("Не удалось обновить экран: %s", exc)
            screen = await send_screen_message(
                message,
                text,
                reply_markup=reply_markup,
            )
            context["screen_message_id"] = screen.message_id
            await _save_context(state, context)
    else:
        screen = await send_screen_message(
            message,
            text,
            reply_markup=reply_markup,
        )
        context["screen_message_id"] = screen.message_id
        await _save_context(state, context)


async def _render_workers_list(message: types.Message, state: FSMContext) -> None:
    context = await _get_context(state)
    selected_ids: list[int] = context.get("selected_worker_ids", []) or []
    workers_map = _worker_map(context)
    selected_workers = [workers_map[w_id] for w_id in selected_ids if w_id in workers_map]

    if not selected_workers:
        text = "выбранные рабочие:\n—"
    else:
        lines = ["выбранные рабочие:"]
        lines.extend(f"{idx + 1}) {worker.name}" for idx, worker in enumerate(selected_workers))
        text = "\n".join(lines)

    markup = make_list_kb(selected_workers)

    message_id = context.get("workers_message_id")
    if isinstance(message_id, int) and message_id > 0:
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message_id,
                reply_markup=markup,
            )
        except TelegramBadRequest as exc:
            logger.debug("Не удалось обновить сообщение списка: %s", exc)
            sent = await message.answer(text, reply_markup=markup)
            remember_message(message.chat.id, sent.message_id)
            context["workers_message_id"] = sent.message_id
            await _save_context(state, context)
    else:
        sent = await message.answer(text, reply_markup=markup)
        remember_message(message.chat.id, sent.message_id)
        context["workers_message_id"] = sent.message_id
        await _save_context(state, context)


async def _return_to_menu(message: types.Message, state: FSMContext) -> None:
    context = await _get_context(state)
    user_id = context.get("user_id")
    row = context.get("row")
    if not isinstance(user_id, int) or not isinstance(row, int):
        await message.answer("Не удалось определить смену для возврата в меню.")
        return

    from bot.handlers.shift_menu import render_shift_menu

    await cleanup_screen(message.bot, message.chat.id, keep_start=False)
    await state.clear()
    service = _get_service()
    base_service = getattr(service, "base_service", lambda: service)()

    await render_shift_menu(
        message,
        user_id,
        row,
        state=state,
        delete_trigger_message=False,
        show_progress=False,
        use_screen_message=True,
        service=base_service,
    )


async def _go_home(message: types.Message, state: FSMContext) -> None:
    from bot.handlers.dashboard import show_dashboard

    await cleanup_screen(message.bot, message.chat.id, keep_start=False)
    await state.clear()
    await show_dashboard(message, state=state)


def _screen_intro() -> str:
    return (
        "👥 Состав бригады — ввод данных\n\n"
        "Добавляйте рабочих в состав смены и подтверждайте список перед сохранением.\n"
        "Используйте кнопку очистки, чтобы начать заново, или подтвердите готовый список."
    )


async def _show_intro(message: types.Message, state: FSMContext) -> None:
    await _update_screen(
        message,
        state,
        text=_screen_intro(),
        reply_markup=crew_start_keyboard(),
    )


async def _show_confirm_screen(message: types.Message, state: FSMContext) -> None:
    context = await _get_context(state)
    workers_map = _worker_map(context)
    selected_ids: list[int] = context.get("selected_worker_ids", []) or []
    selected_workers = [workers_map[w_id] for w_id in selected_ids if w_id in workers_map]
    driver = context.get("driver") or "—"

    lines = [
        "Проверьте состав бригады:",
        f"водитель: {driver or '—'}",
        "рабочие:",
    ]
    if selected_workers:
        lines.extend(f"- {worker.name}" for worker in selected_workers)
    else:
        lines.append("- —")
    lines.append("")
    lines.append("Сохранить?")

    await _update_screen(
        message,
        state,
        text="\n".join(lines),
        reply_markup=crew_confirm_keyboard(),
    )


async def _prompt_worker_selection(message: types.Message, state: FSMContext) -> None:
    context = await _get_context(state)
    workers_map = _worker_map(context)
    if not workers_map:
        reply = await message.answer("Справочник рабочих пуст. Обратитесь к координатору.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    lines = ["Укажите номер рабочего из списка:"]
    for worker in workers_map.values():
        lines.append(f"{worker.worker_id}) {worker.name}")

    prompt = await message.answer("\n".join(lines))
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(CrewState.AWAIT_WORKER)


async def _add_worker_by_id(message: types.Message, state: FSMContext, worker_id: int) -> None:
    context = await _get_context(state)
    selected_ids: list[int] = context.get("selected_worker_ids", []) or []
    workers_map = _worker_map(context)
    worker = workers_map.get(worker_id)
    if worker is None:
        reply = await message.answer("Рабочий с таким номером не найден. Используйте список выше.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    if worker_id in selected_ids:
        reply = await message.answer("Этот рабочий уже выбран.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    selected_ids.append(worker_id)
    context["selected_worker_ids"] = selected_ids
    await _save_context(state, context)

    await _cleanup_step(message, state)
    await flash_message(message, f"Добавлено: {worker.name}", ttl=1.5)
    await _render_workers_list(message, state)
    await _show_intro(message, state)
    await state.set_state(CrewState.WORKERS)


async def _remove_worker(message: types.Message, state: FSMContext, worker_id: int) -> str | None:
    context = await _get_context(state)
    selected_ids: list[int] = context.get("selected_worker_ids", []) or []
    workers_map = _worker_map(context)
    if worker_id not in selected_ids:
        return None
    selected_ids = [wid for wid in selected_ids if wid != worker_id]
    context["selected_worker_ids"] = selected_ids
    await _save_context(state, context)
    await _render_workers_list(message, state)
    worker = workers_map.get(worker_id)
    return worker.name if worker else None


async def _complete_crew(message: types.Message, state: FSMContext) -> None:
    context = await _get_context(state)
    user_id = context.get("user_id")
    row = context.get("row")
    selected_ids: list[int] = context.get("selected_worker_ids", []) or []
    workers_map = _worker_map(context)
    workers = [workers_map[w_id] for w_id in selected_ids if w_id in workers_map]
    driver = context.get("driver")

    if not isinstance(user_id, int) or not isinstance(row, int):
        reply = await message.answer("Не удалось определить смену для сохранения.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return
    if not workers:
        reply = await message.answer("Нужно выбрать хотя бы одного рабочего.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        await _show_intro(message, state)
        await state.set_state(CrewState.WORKERS)
        return

    await flash_message(message, "💾 Сохраняю…", ttl=2.0)
    service = _get_service()
    try:
        await asyncio.to_thread(
            service.save_crew,
            row,
            driver=driver or "",
            workers=[worker.name for worker in workers],
            telegram_id=user_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сохранить состав бригады")
        reply = await message.answer(
            "Не удалось сохранить состав бригады. Попробуйте позже или обратитесь к координатору.",
        )
        await _add_bot_message(state, reply.message_id, message.chat.id)
        await _show_intro(message, state)
        await state.set_state(CrewState.WORKERS)
        return

    from bot.handlers.shift_menu import mark_mode_done, render_shift_menu

    mark_mode_done(user_id, "crew")

    await cleanup_screen(message.bot, message.chat.id, keep_start=False)

    done_message = await message.answer("Состав бригады сохранён ✅")
    await state.clear()

    base_service = getattr(service, "base_service", lambda: service)()

    await render_shift_menu(
        message,
        user_id,
        row,
        state=state,
        delete_trigger_message=False,
        show_progress=False,
        use_screen_message=True,
        service=base_service,
    )

    if done_message:
        try:
            await message.bot.delete_message(message.chat.id, done_message.message_id)
        except TelegramBadRequest:
            pass
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось удалить итоговое сообщение бригады", exc_info=True)


# Обработчики ----------------------------------------------------------------


@router.message(Command("crew"))
async def start_crew(message: types.Message, state: FSMContext, *, user_id: int | None = None) -> None:
    await safe_delete(message)
    await cleanup_screen(message.bot, message.chat.id, keep_start=True)

    actual_user = user_id or (message.from_user.id if message.from_user else None)
    if actual_user is None:
        await message.answer("Не удалось определить пользователя. Откройте смену заново.")
        await state.clear()
        return

    service = _get_service()

    try:
        row = await asyncio.to_thread(service.get_shift_row_index_for_user, actual_user)
        if row is None:
            row = await asyncio.to_thread(service.open_shift_for_user, actual_user)
        workers = await asyncio.to_thread(service.list_active_workers)
        summary = await asyncio.to_thread(service.get_shift_summary, row)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось подготовить данные раздела «Бригада»")
        await message.answer(
            "Не удалось открыть раздел «Бригада». Попробуйте позже или обратитесь к координатору.",
        )
        await state.clear()
        return

    crew_info = summary.get("crew") if isinstance(summary, dict) else None
    driver_name = ""
    selected_names: list[str] = []
    if isinstance(crew_info, dict):
        driver = crew_info.get("driver")
        if isinstance(driver, str):
            driver_name = driver.strip()
        workers_line = crew_info.get("workers")
        if isinstance(workers_line, list):
            selected_names = [str(item).strip() for item in workers_line if str(item).strip()]
        elif isinstance(workers_line, str):
            pieces = [piece.strip() for piece in workers_line.split(",")]
            selected_names = [piece for piece in pieces if piece]

    worker_map = {worker.name.casefold(): worker.worker_id for worker in workers}
    selected_ids = [worker_map[name.casefold()] for name in selected_names if name.casefold() in worker_map]

    context = {
        "user_id": actual_user,
        "row": row,
        "workers": [_serialize_worker(worker) for worker in workers],
        "selected_worker_ids": selected_ids,
        "driver": driver_name,
        "tracker": _make_tracker(),
        "screen_message_id": None,
        "workers_message_id": None,
    }
    await _save_context(state, context)

    await _show_intro(message, state)
    await _render_workers_list(message, state)
    await state.set_state(CrewState.WORKERS)


@router.message(CrewState.WORKERS)
async def handle_workers_menu(message: types.Message, state: FSMContext) -> None:
    text = (message.text or "").strip()

    if text == BACK_BUTTON:
        await _add_user_message(state, message.message_id, message.chat.id)
        await _return_to_menu(message, state)
        return
    if text == SHIFT_BACK_BUTTON:
        await _add_user_message(state, message.message_id, message.chat.id)
        await _go_home(message, state)
        return
    if text == CONFIRM_BUTTON:
        await _add_user_message(state, message.message_id, message.chat.id)
        await _show_confirm_screen(message, state)
        await state.set_state(CrewState.CONFIRM)
        return
    if text == CLEAR_WORKERS_BUTTON:
        await _add_user_message(state, message.message_id, message.chat.id)
        context = await _get_context(state)
        context["selected_worker_ids"] = []
        await _save_context(state, context)
        await _render_workers_list(message, state)
        await flash_message(message, "Список рабочих очищен", ttl=1.5)
        return
    if text == ADD_WORKER_BUTTON:
        await _add_user_message(state, message.message_id, message.chat.id)
        await _prompt_worker_selection(message, state)
        return

    reply = await message.answer("Используйте кнопки на клавиатуре режима.")
    await _add_user_message(state, message.message_id, message.chat.id)
    await _add_bot_message(state, reply.message_id, message.chat.id)


@router.message(CrewState.AWAIT_WORKER)
async def handle_worker_number(message: types.Message, state: FSMContext) -> None:
    await _add_user_message(state, message.message_id, message.chat.id)
    text = (message.text or "").strip()

    if text == BACK_BUTTON:
        await _cleanup_step(message, state)
        await _show_intro(message, state)
        await state.set_state(CrewState.WORKERS)
        return
    if text == SHIFT_BACK_BUTTON:
        await _cleanup_step(message, state)
        await _go_home(message, state)
        return

    try:
        worker_id = int(text)
    except ValueError:
        reply = await message.answer("Введите номер рабочего цифрами.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    await _add_worker_by_id(message, state, worker_id)


@router.message(CrewState.CONFIRM)
async def handle_confirm(message: types.Message, state: FSMContext) -> None:
    await _add_user_message(state, message.message_id, message.chat.id)
    text = (message.text or "").strip()

    if text == EDIT_BUTTON:
        await _cleanup_step(message, state)
        await _show_intro(message, state)
        await state.set_state(CrewState.WORKERS)
        return
    if text == SHIFT_BACK_BUTTON:
        await _cleanup_step(message, state)
        await _go_home(message, state)
        return
    if text == CONFIRM_BUTTON:
        await _cleanup_step(message, state)
        await _complete_crew(message, state)
        return

    reply = await message.answer("Используйте кнопки подтверждения.")
    await _add_bot_message(state, reply.message_id, message.chat.id)


@router.callback_query(F.data.startswith(REMOVE_PREFIX))
async def handle_remove_worker(callback: types.CallbackQuery, state: FSMContext) -> None:
    payload = callback.data or ""
    tail = payload[len(REMOVE_PREFIX) :]
    try:
        worker_id = int(tail)
    except ValueError:
        await callback.answer("не найдено", show_alert=False)
        return

    message = callback.message
    if message is None:
        await callback.answer("не найдено", show_alert=False)
        return

    removed_name = await _remove_worker(message, state, worker_id)
    if removed_name:
        await callback.answer(f"Удалено: {removed_name}")
    else:
        await callback.answer("не найдено", show_alert=False)
