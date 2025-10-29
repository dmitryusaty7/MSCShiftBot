"""Reply-сценарий раздела «Бригада»: интро → водитель → рабочие."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, Sequence

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.crew_reply import (
    ADD_DRIVER_BUTTON,
    BACK_BUTTON,
    CLEAR_WORKERS_BUTTON,
    CONFIRM_BUTTON,
    MENU_BUTTON,
    NEXT_BUTTON,
    START_BUTTON,
    make_driver_kb,
    make_intro_kb,
    make_workers_kb,
)
from bot.services import CrewSheetsService, CrewWorker
from bot.utils.cleanup import cleanup_screen, send_screen_message
from bot.utils.flash import flash_message, start_mode_flash
from features.utils.messaging import safe_delete

router = Router(name="crew")
logger = logging.getLogger(__name__)


class CrewState(StatesGroup):
    """Этапы сценария раздела «Бригада»"""

    INTRO = State()
    DRIVER = State()
    WORKERS = State()


_service: CrewSheetsService | None = None


# ---------------------------------------------------------------------------
# Вспомогательные функции


def _get_service() -> CrewSheetsService:
    global _service
    if _service is None:
        _service = CrewSheetsService()
    return _service


def _serialize_workers(workers: Sequence[CrewWorker]) -> list[dict[str, Any]]:
    return [{"id": worker.worker_id, "name": worker.name} for worker in workers]


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
            logger.debug("Не удалось обновить экран раздела «Бригада»: %s", exc)

    screen = await send_screen_message(message, text, reply_markup=reply_markup)
    await state.update_data(crew_screen_id=screen.message_id)


async def _flash(
    target: types.Message,
    state: FSMContext,
    text: str,
    *,
    ttl: float = 2.0,
) -> None:
    flash = await flash_message(target, text, ttl=ttl)
    data = await state.get_data()
    ephemeral = data.get("crew_ephemeral_ids")
    if not isinstance(ephemeral, list):
        ephemeral_list: list[int] = []
    else:
        ephemeral_list = list(ephemeral)
    ephemeral_list.append(flash.message_id)
    await state.update_data(crew_ephemeral_ids=ephemeral_list[-10:])


def _intro_text() -> str:
    return (
        "👥 Состав бригады — ввод данных\n\n"
        "Заполните состав смены по шагам: сначала выберите водителя, затем — рабочих.\n"
        "После выбора проверьте сводку и подтвердите изменения."
    )


def _driver_step_text(driver: CrewWorker | None) -> str:
    current = driver.name if driver else "не выбран"
    return (
        "🚚 Водитель\n"
        "выберите водителя из списка\n"
        f"текущий выбор: {current}"
    )


def _workers_step_text(driver: CrewWorker | None, selected: Sequence[CrewWorker]) -> str:
    driver_name = driver.name if driver else "—"
    lines = [
        "🧑‍🔧 Рабочие",
        f"водитель: {driver_name}",
        f"выбрано: {len(selected)}",
        "",
        "тап по имени — добавляет/удаляет из списка",
    ]
    if selected:
        lines.append("")
        lines.append("выбранные:")
        lines.extend(f"• {worker.name}" for worker in selected)
    return "\n".join(lines)


async def _show_intro(message: types.Message, state: FSMContext) -> None:
    await _set_screen_message(message, state, text=_intro_text(), reply_markup=make_intro_kb())
    await state.update_data(crew_map_buttons={})
    await state.set_state(CrewState.INTRO)


async def _show_driver_step(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    if not drivers:
        await _flash(message, state, "Справочник водителей пуст. Обратитесь к координатору.")
        await _show_intro(message, state)
        return

    driver_id = data.get("crew_driver_id") if isinstance(data.get("crew_driver_id"), int) else None
    drivers_map = _workers_map(drivers)
    markup, mapping = make_driver_kb(drivers, driver_id)
    driver = drivers_map.get(driver_id) if driver_id else None

    await _set_screen_message(message, state, text=_driver_step_text(driver), reply_markup=markup)
    await state.update_data(crew_map_buttons=mapping)
    await state.set_state(CrewState.DRIVER)


async def _show_workers_step(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    workers = _deserialize_workers(data.get("crew_workers"))
    driver_id = data.get("crew_driver_id")
    if not isinstance(driver_id, int):
        await _flash(message, state, "Сначала выберите водителя.")
        await _show_driver_step(message, state)
        return
    if not workers:
        await _flash(message, state, "Справочник рабочих пуст. Обратитесь к координатору.")
        await _show_driver_step(message, state)
        return

    raw_selected = data.get("crew_selected_worker_ids") or []
    selected_ids: list[int] = []
    for value in raw_selected:
        if isinstance(value, int):
            selected_ids.append(value)
        elif isinstance(value, str) and value.isdigit():
            selected_ids.append(int(value))

    worker_map = _workers_map(workers)
    selected_workers = [worker_map[w_id] for w_id in selected_ids if w_id in worker_map]
    markup, mapping = make_workers_kb(workers, selected_ids)
    driver = _workers_map(drivers).get(driver_id)

    await _set_screen_message(
        message,
        state,
        text=_workers_step_text(driver, selected_workers),
        reply_markup=markup,
    )
    await state.update_data(crew_map_buttons=mapping)
    await state.set_state(CrewState.WORKERS)


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


async def _return_to_shift_menu(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data.get("crew_user_id")
    row = data.get("crew_row")
    if not isinstance(user_id, int) or not isinstance(row, int):
        await flash_message(message, "Не удалось определить смену для возврата.")
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
    selected_ids = data.get("crew_selected_worker_ids") or []

    if not isinstance(row, int) or not isinstance(user_id, int):
        await _flash(message, state, "Не удалось определить смену для сохранения.")
        return

    driver = _workers_map(drivers).get(driver_id) if isinstance(driver_id, int) else None
    worker_map = _workers_map(workers)
    selected_workers = [worker_map.get(w_id) for w_id in selected_ids]
    selected_workers = [worker for worker in selected_workers if worker is not None]

    if not driver:
        await _flash(message, state, "Нужно выбрать водителя перед сохранением.")
        await _show_driver_step(message, state)
        return
    if not selected_workers:
        await _flash(message, state, "Выберите хотя бы одного рабочего.")
        await _show_workers_step(message, state)
        return

    summary_lines = [
        "Проверьте состав бригады:",
        f"водитель: {driver.name}",
        "рабочие:",
    ]
    summary_lines.extend(f"- {worker.name}" for worker in selected_workers)
    await _set_screen_message(message, state, text="\n".join(summary_lines), reply_markup=make_intro_kb())

    service = _get_service()
    try:
        await asyncio.to_thread(
            service.save_crew,
            row,
            driver=driver.name,
            workers=[worker.name for worker in selected_workers],
            telegram_id=user_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сохранить состав бригады (row=%s, user_id=%s)", row, user_id)
        await _flash(message, state, "Не удалось сохранить состав. Попробуйте позже.", ttl=2.5)
        await _show_workers_step(message, state)
        return

    await flash_message(message, "💾 Сохраняю…", ttl=2)
    await flash_message(message, "✅ Состав бригады сохранён", ttl=2)
    await _return_to_shift_menu(message, state)


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
        crew_ephemeral_ids=[],
        crew_map_buttons={},
    )

    await start_mode_flash(message, "crew", ttl=1.5)
    await _show_intro(message, state)


@router.message(CrewState.INTRO, F.text == START_BUTTON)
async def handle_intro_start(message: types.Message, state: FSMContext) -> None:
    await safe_delete(message)
    await _show_driver_step(message, state)


@router.message(CrewState.INTRO, F.text == MENU_BUTTON)
async def handle_intro_menu(message: types.Message, state: FSMContext) -> None:
    await safe_delete(message)
    await _return_to_shift_menu(message, state)


@router.message(CrewState.DRIVER)
async def handle_driver_step(message: types.Message, state: FSMContext) -> None:
    await safe_delete(message)
    text = (message.text or "").strip()

    if text == MENU_BUTTON:
        await _return_to_shift_menu(message, state)
        return
    if text == BACK_BUTTON:
        await _show_intro(message, state)
        return
    if text == ADD_DRIVER_BUTTON:
        await _flash(message, state, "Добавление водителя пока недоступно.")
        await _show_driver_step(message, state)
        return
    if text == NEXT_BUTTON:
        data = await state.get_data()
        driver_id = data.get("crew_driver_id")
        if isinstance(driver_id, int):
            await _show_workers_step(message, state)
        else:
            await _flash(message, state, "Сначала выберите водителя.")
            await _show_driver_step(message, state)
        return

    data = await state.get_data()
    mapping = data.get("crew_map_buttons") or {}
    drivers = _deserialize_workers(data.get("crew_drivers"))
    driver_map = _workers_map(drivers)
    if text in mapping:
        driver_id = mapping[text]
        driver_name = driver_map.get(driver_id).name if driver_map.get(driver_id) else str(driver_id)
        await state.update_data(crew_driver_id=driver_id)
        await _flash(message, state, f"✔ водитель выбран: {driver_name}")
        await _show_driver_step(message, state)
        return

    await _flash(message, state, "Пожалуйста, используйте кнопки ниже.")
    await _show_driver_step(message, state)


@router.message(CrewState.WORKERS)
async def handle_workers_step(message: types.Message, state: FSMContext) -> None:
    await safe_delete(message)
    text = (message.text or "").strip()

    if text == MENU_BUTTON:
        await _return_to_shift_menu(message, state)
        return
    if text == BACK_BUTTON:
        await _show_driver_step(message, state)
        return
    if text == CLEAR_WORKERS_BUTTON:
        await state.update_data(crew_selected_worker_ids=[])
        await _flash(message, state, "Список рабочих очищен.")
        await _show_workers_step(message, state)
        return
    if text == CONFIRM_BUTTON:
        await _save_crew(message, state)
        return

    data = await state.get_data()
    mapping = data.get("crew_map_buttons") or {}
    workers = _deserialize_workers(data.get("crew_workers"))
    worker_map = _workers_map(workers)

    if text in mapping:
        worker_id = mapping[text]
        selected = data.get("crew_selected_worker_ids") or []
        selected_set: set[int] = set()
        for value in selected:
            if isinstance(value, int):
                selected_set.add(value)
            elif isinstance(value, str) and value.isdigit():
                selected_set.add(int(value))
        worker = worker_map.get(worker_id)
        worker_name = worker.name if worker else str(worker_id)
        if worker_id in selected_set:
            selected_set.remove(worker_id)
            await _flash(message, state, f"✖ удалён {worker_name}")
        else:
            selected_set.add(worker_id)
            await _flash(message, state, f"✔ добавлен {worker_name}")
        await state.update_data(crew_selected_worker_ids=sorted(selected_set))
        await _show_workers_step(message, state)
        return

    await _flash(message, state, "Пожалуйста, используйте кнопки ниже.")
    await _show_workers_step(message, state)
