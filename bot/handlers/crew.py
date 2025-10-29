"""Пошаговый сценарий раздела «Бригада»: водитель → рабочие → подтверждение."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Sequence

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.crew_inline import (
    CONFIRM_CALLBACK,
    DRIVER_ADD_CALLBACK,
    DRIVER_LIST_PREFIX,
    DRIVER_PICK_PREFIX,
    NAV_BACK_CALLBACK,
    NAV_HOME_CALLBACK,
    NOOP_CALLBACK,
    WORKER_ADD_CALLBACK,
    WORKER_CLEAR_CALLBACK,
    WORKER_LIST_PREFIX,
    WORKER_TOGGLE_PREFIX,
    build_driver_keyboard,
    build_worker_keyboard,
)
from bot.keyboards.crew_reply import (
    ADD_WORKER_BUTTON,
    BACK_BUTTON,
    CLEAR_WORKERS_BUTTON,
    CONFIRM_BUTTON,
    EDIT_BUTTON,
    MENU_BUTTON,
    crew_confirm_keyboard,
    crew_start_keyboard,
)
from bot.services import CrewSheetsService, CrewWorker
from bot.utils.cleanup import cleanup_screen, remember_message, send_screen_message
from bot.utils.flash import flash_message
from features.utils.messaging import safe_delete

router = Router(name="crew")
logger = logging.getLogger(__name__)


class CrewState(StatesGroup):
    """Состояния сценария «Бригада»"""

    DRIVER = State()
    WORKERS = State()
    CONFIRM = State()


_service: CrewSheetsService | None = None


# ---------------------------------------------------------------------------
# Служебные функции работы с данными состояния


def _get_service() -> CrewSheetsService:
    global _service
    if _service is None:
        _service = CrewSheetsService()
    return _service


def _serialize_workers(workers: Sequence[CrewWorker]) -> list[dict[str, Any]]:
    return [
        {
            "id": worker.worker_id,
            "name": worker.name,
        }
        for worker in workers
    ]


def _deserialize_workers(raw: Iterable[dict[str, Any]] | None) -> list[CrewWorker]:
    if not raw:
        return []
    result: list[CrewWorker] = []
    for item in raw:
        worker_id = item.get("id") if isinstance(item, dict) else None
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(worker_id, int) and isinstance(name, str):
            result.append(CrewWorker(worker_id=worker_id, name=name))
    return result


def _workers_map(workers: Sequence[CrewWorker]) -> dict[int, CrewWorker]:
    return {worker.worker_id: worker for worker in workers}


async def _set_screen_message(
    message: types.Message,
    state: FSMContext,
    *,
    text: str,
    reply_markup: Any,
) -> None:
    data = await state.get_data()
    screen_id = data.get("crew_screen_id")
    if isinstance(screen_id, int) and screen_id > 0:
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=screen_id,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as exc:
            logger.debug("Не удалось обновить экран режима «Бригада»: %s", exc)
    screen = await send_screen_message(message, text, reply_markup=reply_markup)
    await state.update_data(crew_screen_id=screen.message_id)


async def _set_inline_message(
    message: types.Message,
    state: FSMContext,
    *,
    text: str,
    reply_markup: Any,
) -> None:
    data = await state.get_data()
    inline_id = data.get("crew_inline_id")
    if isinstance(inline_id, int) and inline_id > 0:
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=inline_id,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as exc:
            logger.debug("Не удалось обновить inline-сообщение бригады: %s", exc)
    inline_message = await message.answer(text, reply_markup=reply_markup)
    remember_message(message.chat.id, inline_message.message_id)
    await state.update_data(crew_inline_id=inline_message.message_id)


async def _resolve_user_id(
    message: types.Message,
    state: FSMContext,
    provided: int | None,
) -> int | None:
    if provided is not None:
        return provided

    if message.from_user and not message.from_user.is_bot:
        return message.from_user.id

    data = await state.get_data()
    for key in ("crew_user_id", "user_id", "_shift_user_id"):
        candidate = data.get(key)
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str) and candidate.isdigit():
            return int(candidate)

    if message.chat.type == "private":
        return message.chat.id
    return None


def _intro_text() -> str:
    return (
        "👥 Состав бригады — ввод данных\n\n"
        "Выберите водителя, затем добавьте рабочих из списка.\n"
        "Перед сохранением проверьте состав и подтвердите изменения."
    )


async def _render_driver_step(message: types.Message, state: FSMContext, *, page: int | None = None) -> None:
    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    if not drivers:
        await flash_message(message, "Справочник водителей пуст. Обратитесь к координатору.", ttl=2)
        return

    selected_driver_id = data.get("crew_driver_id") if isinstance(data.get("crew_driver_id"), int) else None
    target_page = page if page is not None else data.get("crew_driver_page", 0)
    markup, actual_page, total_pages = build_driver_keyboard(
        drivers,
        page=target_page,
        selected_driver_id=selected_driver_id,
    )
    await state.update_data(crew_driver_page=actual_page)

    driver_name = None
    if selected_driver_id:
        driver_name = _workers_map(drivers).get(selected_driver_id)
        driver_name = driver_name.name if driver_name else None

    lines = ["🚚 Водитель", "выберите водителя из списка"]
    if driver_name:
        lines.append(f"текущий выбор: {driver_name}")
    else:
        lines.append("текущий выбор: не выбран")
    if total_pages > 1:
        lines.append(f"страница {actual_page + 1} из {total_pages}")

    await _set_inline_message(message, state, text="\n".join(lines), reply_markup=markup)
    await state.set_state(CrewState.DRIVER)


async def _render_worker_step(
    message: types.Message,
    state: FSMContext,
    *,
    page: int | None = None,
) -> None:
    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    workers = _deserialize_workers(data.get("crew_workers"))
    driver_id = data.get("crew_driver_id")
    if not isinstance(driver_id, int):
        await flash_message(message, "Сначала выберите водителя.", ttl=2)
        await _render_driver_step(message, state)
        return

    if not workers:
        await flash_message(message, "Справочник рабочих пуст. Обратитесь к координатору.", ttl=2)
        await _render_driver_step(message, state)
        return

    selected_ids = data.get("crew_selected_worker_ids", []) or []
    target_page = page if page is not None else data.get("crew_worker_page", 0)
    markup, actual_page, total_pages = build_worker_keyboard(
        workers,
        page=target_page,
        selected_ids=selected_ids,
    )
    await state.update_data(crew_worker_page=actual_page)

    driver = _workers_map(drivers).get(driver_id)
    driver_name = driver.name if driver else "—"
    selected_workers = [_workers_map(workers).get(w_id) for w_id in selected_ids]
    selected_workers = [worker for worker in selected_workers if worker is not None]

    lines = [
        "🧑‍🔧 Рабочие",
        f"водитель: {driver_name}",
        f"выбрано: {len(selected_workers)}",
        "",
        "тап по имени — добавляет/удаляет из списка",
    ]
    if selected_workers:
        lines.append("")
        lines.append("выбранные:")
        lines.extend(f"• {worker.name}" for worker in selected_workers)
    if total_pages > 1:
        lines.append("")
        lines.append(f"страница {actual_page + 1} из {total_pages}")

    await _set_inline_message(message, state, text="\n".join(lines), reply_markup=markup)
    await state.set_state(CrewState.WORKERS)


async def _show_confirm_screen(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    workers = _deserialize_workers(data.get("crew_workers"))
    driver_id = data.get("crew_driver_id")
    selected_ids = data.get("crew_selected_worker_ids", []) or []
    driver = _workers_map(drivers).get(driver_id) if isinstance(driver_id, int) else None
    worker_map = _workers_map(workers)
    selected_workers = [worker_map.get(w_id) for w_id in selected_ids]
    selected_workers = [worker for worker in selected_workers if worker is not None]

    lines = [
        "Проверьте состав бригады:",
        f"водитель: {driver.name if driver else '—'}",
        "рабочие:",
    ]
    if selected_workers:
        lines.extend(f"- {worker.name}" for worker in selected_workers)
    else:
        lines.append("- —")
    lines.append("")
    lines.append("Сохранить изменения?")

    await _set_screen_message(message, state, text="\n".join(lines), reply_markup=crew_confirm_keyboard())
    await state.set_state(CrewState.CONFIRM)


async def _show_intro(message: types.Message, state: FSMContext) -> None:
    await _set_screen_message(message, state, text=_intro_text(), reply_markup=crew_start_keyboard())


async def _return_to_shift_menu(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data.get("crew_user_id")
    row = data.get("crew_row")
    if not isinstance(user_id, int) or not isinstance(row, int):
        await flash_message(message, "Не удалось определить смену для возврата.", ttl=2)
        return

    from bot.handlers.shift_menu import render_shift_menu

    service = _get_service()
    base_service = service.base_service()

    await cleanup_screen(message.bot, message.chat.id, keep_start=False)
    await state.clear()

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


async def _save_crew(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    row = data.get("crew_row")
    user_id = data.get("crew_user_id")
    drivers = _deserialize_workers(data.get("crew_drivers"))
    workers = _deserialize_workers(data.get("crew_workers"))
    driver_id = data.get("crew_driver_id")
    selected_ids = data.get("crew_selected_worker_ids", []) or []

    if not isinstance(row, int) or not isinstance(user_id, int):
        await flash_message(message, "Не удалось определить смену для сохранения.", ttl=2)
        return

    worker_map = _workers_map(workers)
    selected_workers = [worker_map.get(w_id) for w_id in selected_ids]
    selected_workers = [worker for worker in selected_workers if worker is not None]
    if not selected_workers:
        await flash_message(message, "Нужно выбрать хотя бы одного рабочего.", ttl=2)
        await _render_worker_step(message, state)
        return

    driver = _workers_map(drivers).get(driver_id) if isinstance(driver_id, int) else None
    driver_name = driver.name if driver else ""

    service = _get_service()
    try:
        await asyncio.to_thread(
            service.save_crew,
            row,
            driver=driver_name,
            workers=[worker.name for worker in selected_workers],
            telegram_id=user_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сохранить состав бригады (row=%s, user_id=%s)", row, user_id)
        await flash_message(message, "Не удалось сохранить состав. Попробуйте позже.", ttl=2.5)
        return

    await flash_message(message, "💾 Сохраняю…", ttl=2)
    await flash_message(message, "✅ Состав бригады сохранён", ttl=2)
    await _return_to_shift_menu(message, state)


async def _handle_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


# ---------------------------------------------------------------------------
# Точки входа и обработчики


@router.message(Command("crew"))
async def start_crew(
    message: types.Message,
    state: FSMContext,
    *,
    user_id: int | None = None,
) -> None:
    await safe_delete(message)

    actual_user_id = await _resolve_user_id(message, state, user_id)
    if actual_user_id is None:
        await message.answer("Не удалось определить пользователя. Начните смену заново.")
        await state.clear()
        return

    service = _get_service()
    try:
        row = await asyncio.to_thread(service.get_shift_row_index_for_user, actual_user_id)
        if row is None:
            row = await asyncio.to_thread(service.open_shift_for_user, actual_user_id)
        drivers = await asyncio.to_thread(service.list_active_drivers)
        workers = await asyncio.to_thread(service.list_active_workers)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось подготовить данные раздела «Бригада» (user_id=%s)", actual_user_id)
        await message.answer("Не удалось открыть раздел «Бригада». Попробуйте позже.")
        await state.clear()
        return

    await state.update_data(
        crew_user_id=actual_user_id,
        crew_row=row,
        crew_drivers=_serialize_workers(drivers),
        crew_workers=_serialize_workers(workers),
        crew_driver_id=None,
        crew_selected_worker_ids=[],
        crew_screen_id=None,
        crew_inline_id=None,
        crew_driver_page=0,
        crew_worker_page=0,
    )

    await _show_intro(message, state)
    await _render_driver_step(message, state)


# -------------------------- Inline обработчики -----------------------------


@router.callback_query(F.data == NOOP_CALLBACK)
async def handle_noop(callback: types.CallbackQuery, state: FSMContext) -> None:  # noqa: ARG001
    await _handle_noop(callback)


@router.callback_query(F.data == NAV_HOME_CALLBACK)
async def handle_nav_home(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await _return_to_shift_menu(callback.message, state)


@router.callback_query(F.data == NAV_BACK_CALLBACK)
async def handle_nav_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    current_state = await state.get_state()
    if current_state == CrewState.WORKERS.state:
        await _show_intro(callback.message, state)
        await _render_driver_step(callback.message, state)
    elif current_state == CrewState.CONFIRM.state:
        await _show_intro(callback.message, state)
        await _render_worker_step(callback.message, state)
    else:
        await _show_intro(callback.message, state)


@router.callback_query(F.data.startswith(DRIVER_PICK_PREFIX))
async def handle_driver_pick(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    suffix = callback.data[len(DRIVER_PICK_PREFIX) :] if callback.data else ""
    try:
        driver_id = int(suffix)
    except ValueError:
        await flash_message(callback.message, "Не удалось определить водителя.", ttl=2)
        return

    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    if driver_id not in _workers_map(drivers):
        await flash_message(callback.message, "Такого водителя нет в списке.", ttl=2)
        return

    await state.update_data(crew_driver_id=driver_id)
    await _render_driver_step(callback.message, state)


@router.callback_query(F.data.startswith(DRIVER_LIST_PREFIX))
async def handle_driver_page(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    suffix = callback.data[len(DRIVER_LIST_PREFIX) :] if callback.data else "0"
    try:
        page = int(suffix)
    except ValueError:
        page = 0
    await _render_driver_step(callback.message, state, page=page)


@router.callback_query(F.data == DRIVER_ADD_CALLBACK)
async def handle_driver_add(callback: types.CallbackQuery, state: FSMContext) -> None:  # noqa: ARG001
    await callback.answer()
    if callback.message:
        await flash_message(callback.message, "Добавление водителя пока недоступно.", ttl=2)


@router.callback_query(F.data.startswith(WORKER_LIST_PREFIX))
async def handle_worker_page(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    suffix = callback.data[len(WORKER_LIST_PREFIX) :] if callback.data else "0"
    try:
        page = int(suffix)
    except ValueError:
        page = 0
    await _render_worker_step(callback.message, state, page=page)


@router.callback_query(F.data.startswith(WORKER_TOGGLE_PREFIX))
async def handle_worker_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    suffix = callback.data[len(WORKER_TOGGLE_PREFIX) :] if callback.data else ""
    try:
        worker_id = int(suffix)
    except ValueError:
        await flash_message(callback.message, "Не удалось определить рабочего.", ttl=2)
        return

    data = await state.get_data()
    workers = _deserialize_workers(data.get("crew_workers"))
    workers_map = _workers_map(workers)
    if worker_id not in workers_map:
        await flash_message(callback.message, "Рабочий не найден.", ttl=2)
        return

    selected_ids = list(data.get("crew_selected_worker_ids", []) or [])
    if worker_id in selected_ids:
        selected_ids = [wid for wid in selected_ids if wid != worker_id]
    else:
        selected_ids.append(worker_id)

    await state.update_data(crew_selected_worker_ids=selected_ids)
    await _render_worker_step(callback.message, state)


@router.callback_query(F.data == WORKER_CLEAR_CALLBACK)
async def handle_worker_clear(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.update_data(crew_selected_worker_ids=[])
    await _render_worker_step(callback.message, state)


@router.callback_query(F.data == WORKER_ADD_CALLBACK)
async def handle_worker_add(callback: types.CallbackQuery, state: FSMContext) -> None:  # noqa: ARG001
    await callback.answer()
    if callback.message:
        await flash_message(callback.message, "Добавление рабочего пока недоступно.", ttl=2)


@router.callback_query(F.data == CONFIRM_CALLBACK)
async def handle_inline_confirm(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    data = await state.get_data()
    selected_ids = data.get("crew_selected_worker_ids", []) or []
    if not selected_ids:
        await flash_message(callback.message, "Нужно выбрать хотя бы одного рабочего.", ttl=2)
        return
    await _show_confirm_screen(callback.message, state)


# --------------------------- Reply обработчики -----------------------------


@router.message(CrewState.CONFIRM, F.text == CONFIRM_BUTTON)
async def handle_confirm_save(message: types.Message, state: FSMContext) -> None:
    await _save_crew(message, state)


@router.message(CrewState.CONFIRM, F.text == EDIT_BUTTON)
async def handle_confirm_edit(message: types.Message, state: FSMContext) -> None:
    await _show_intro(message, state)
    await _render_worker_step(message, state)


@router.message(F.text == ADD_WORKER_BUTTON)
async def handle_add_worker_button(message: types.Message, state: FSMContext) -> None:
    await _render_worker_step(message, state)


@router.message(F.text == CLEAR_WORKERS_BUTTON)
async def handle_clear_button(message: types.Message, state: FSMContext) -> None:
    await state.update_data(crew_selected_worker_ids=[])
    await _render_worker_step(message, state)


@router.message(F.text == CONFIRM_BUTTON)
async def handle_confirm_button(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == CrewState.CONFIRM.state:
        await _save_crew(message, state)
        return
    data = await state.get_data()
    selected_ids = data.get("crew_selected_worker_ids", []) or []
    if not selected_ids:
        await flash_message(message, "Нужно выбрать хотя бы одного рабочего.", ttl=2)
        return
    await _show_confirm_screen(message, state)


@router.message(F.text == BACK_BUTTON)
async def handle_back_button(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == CrewState.WORKERS.state:
        await _show_intro(message, state)
        await _render_driver_step(message, state)
    elif current_state == CrewState.CONFIRM.state:
        await _show_intro(message, state)
        await _render_worker_step(message, state)
    else:
        await _return_to_shift_menu(message, state)


@router.message(F.text == MENU_BUTTON)
async def handle_menu_button(message: types.Message, state: FSMContext) -> None:
    await _return_to_shift_menu(message, state)
