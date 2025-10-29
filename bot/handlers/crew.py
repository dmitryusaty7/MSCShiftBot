"""Reply-сценарий раздела «Бригада» с интро, выбором водителя и рабочих."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Sequence

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
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
from .crew_norm import norm_text

router = Router(name="crew")
logger = logging.getLogger(__name__)


class CrewState(StatesGroup):
    """Этапы сценария заполнения состава бригады."""

    INTRO = State()
    DRIVER = State()
    WORKERS = State()


_service: CrewSheetsService | None = None


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _get_service() -> CrewSheetsService:
    global _service
    if _service is None:
        _service = CrewSheetsService()
    return _service


async def show_screen(
    message: types.Message,
    state: FSMContext,
    *,
    text: str,
    reply_markup: Any,
) -> None:
    """Отрисовывает/обновляет единственный экран раздела."""

    data = await state.get_data()
    screen_id = data.get("crew_screen_id")

    if isinstance(screen_id, int):
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=screen_id,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest:
            logger.debug("Не удалось обновить сообщение экрана, отправляю новое.")

    screen = await send_screen_message(message, text, reply_markup=reply_markup)
    await state.update_data(crew_screen_id=screen.message_id)


def _serialize_workers(workers: Sequence[CrewWorker]) -> list[dict[str, Any]]:
    return [{"id": worker.worker_id, "name": worker.name} for worker in workers]


def _deserialize_workers(raw: Iterable[dict[str, Any]] | None) -> list[CrewWorker]:
    if not raw:
        return []
    workers: list[CrewWorker] = []
    for item in raw:
        worker_id = item.get("id") if isinstance(item, dict) else None
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(worker_id, int) and isinstance(name, str):
            workers.append(CrewWorker(worker_id=worker_id, name=name))
    return workers


def _resolve_choice(mapping: dict[str, int], text: str | None) -> int | None:
    target = norm_text(text)
    for key, value in mapping.items():
        if norm_text(key) == target:
            return value
    return None


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
        lines.extend(["", "выбранные:"])
        lines.extend(f"• {worker.name}" for worker in selected)
    return "\n".join(lines)


def _summary_text(driver: CrewWorker, workers: Sequence[CrewWorker]) -> str:
    lines = [
        "Проверьте состав бригады:",
        f"водитель: {driver.name}",
        "рабочие:",
    ]
    lines.extend(f"- {worker.name}" for worker in workers)
    return "\n".join(lines)


def _selected_ids(data: dict[str, Any]) -> list[int]:
    raw = data.get("crew_selected_worker_ids") or []
    selected: list[int] = []
    for value in raw:
        if isinstance(value, int):
            selected.append(value)
        elif isinstance(value, str) and value.isdigit():
            selected.append(int(value))
    return selected


async def _enter_intro(message: types.Message, state: FSMContext) -> None:
    await state.update_data(crew_map_buttons={})
    await state.set_state(CrewState.INTRO)
    await show_screen(
        message,
        state,
        text=(
            "👥 Состав бригады — ввод данных\n\n"
            "Заполните состав по шагам: сначала водитель, затем рабочие."
        ),
        reply_markup=make_intro_kb(),
    )


async def _enter_driver_step(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    if not drivers:
        await flash_message(message, "Справочник водителей пуст. Обратитесь к координатору.")
        await _enter_intro(message, state)
        return

    driver_id = data.get("crew_driver_id") if isinstance(data.get("crew_driver_id"), int) else None
    markup, mapping = make_driver_kb(drivers, driver_id)
    driver = next((item for item in drivers if item.worker_id == driver_id), None)

    await state.update_data(crew_map_buttons=mapping)
    await state.set_state(CrewState.DRIVER)
    await show_screen(
        message,
        state,
        text=_driver_step_text(driver),
        reply_markup=markup,
    )


async def _enter_workers_step(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    workers = _deserialize_workers(data.get("crew_workers"))
    driver_id = data.get("crew_driver_id")

    if not isinstance(driver_id, int):
        await flash_message(message, "Сначала выберите водителя.")
        await _enter_driver_step(message, state)
        return
    if not workers:
        await flash_message(message, "Справочник рабочих пуст. Обратитесь к координатору.")
        await _enter_driver_step(message, state)
        return

    selected_ids = _selected_ids(data)
    selected_workers = [worker for worker in workers if worker.worker_id in set(selected_ids)]
    driver = next((item for item in drivers if item.worker_id == driver_id), None)

    markup, mapping = make_workers_kb(workers, selected_ids)

    await state.update_data(crew_map_buttons=mapping)
    await state.set_state(CrewState.WORKERS)
    await show_screen(
        message,
        state,
        text=_workers_step_text(driver, selected_workers),
        reply_markup=markup,
    )


async def _save_and_finish(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    workers = _deserialize_workers(data.get("crew_workers"))
    driver_id = data.get("crew_driver_id")
    selected_ids = _selected_ids(data)

    driver = next((item for item in drivers if item.worker_id == driver_id), None)
    if driver is None:
        await flash_message(message, "Нужно выбрать водителя.")
        await _enter_driver_step(message, state)
        return

    selected_workers = [worker for worker in workers if worker.worker_id in set(selected_ids)]
    if not selected_workers:
        await flash_message(message, "Выберите хотя бы одного рабочего.")
        await _enter_workers_step(message, state)
        return

    await show_screen(
        message,
        state,
        text=_summary_text(driver, selected_workers),
        reply_markup=make_intro_kb(),
    )

    row = data.get("crew_row")
    user_id = data.get("crew_user_id") or (message.from_user.id if message.from_user else None)
    if not isinstance(row, int) or not isinstance(user_id, int):
        await flash_message(message, "Не удалось определить смену для сохранения.")
        await _enter_workers_step(message, state)
        return

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
        await flash_message(message, "Не удалось сохранить состав. Попробуйте позже.", ttl=2.5)
        await _enter_workers_step(message, state)
        return

    await flash_message(message, "💾 Сохраняю…", ttl=1.2)
    await flash_message(message, "✅ Состав бригады сохранён", ttl=1.2)

    await cleanup_screen(message.bot, message.chat.id, keep_start=False)
    await state.clear()

    from bot.handlers import shift_menu

    await shift_menu.render_shift_menu(
        message,
        user_id,
        row,
        service=service.base_service(),
        state=state,
        delete_trigger_message=False,
        show_progress=False,
        use_screen_message=True,
    )


async def _prepare_references(state: FSMContext, user_id: int) -> tuple[int, list[CrewWorker], list[CrewWorker]]:
    service = _get_service()

    try:
        row = await asyncio.to_thread(service.get_shift_row_index_for_user, user_id)
        if row is None:
            row = await asyncio.to_thread(service.open_shift_for_user, user_id)
        drivers = await asyncio.to_thread(service.list_active_drivers)
        workers = await asyncio.to_thread(service.list_active_workers)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось подготовить данные для раздела «Бригада» (user_id=%s)", user_id)
        raise

    await state.update_data(
        crew_row=row,
        crew_drivers=_serialize_workers(drivers),
        crew_workers=_serialize_workers(workers),
    )
    return row, drivers, workers


async def _return_to_menu(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data.get("crew_user_id")
    row = data.get("crew_row")

    await cleanup_screen(message.bot, message.chat.id, keep_start=False)
    await state.clear()

    from bot.handlers import shift_menu

    service = _get_service()

    await shift_menu.render_shift_menu(
        message,
        user_id if isinstance(user_id, int) else message.from_user.id,
        row if isinstance(row, int) else None,
        service=service.base_service(),
        state=state,
        delete_trigger_message=False,
        show_progress=False,
        use_screen_message=True,
    )


# ---------------------------------------------------------------------------
# Публичные обработчики
# ---------------------------------------------------------------------------


async def start_crew(message: types.Message, state: FSMContext, user_id: int) -> None:
    """Запускает режим заполнения состава бригады."""

    await safe_delete(message)
    try:
        await _prepare_references(state, user_id)
    except Exception:  # noqa: BLE001
        await message.answer("Не удалось открыть раздел «Бригада». Попробуйте позже.")
        await state.clear()
        return

    await state.update_data(
        crew_user_id=user_id,
        crew_driver_id=None,
        crew_selected_worker_ids=[],
        crew_map_buttons={},
        crew_screen_id=None,
    )

    await state.set_state(CrewState.INTRO)

    await start_mode_flash(message, "crew")
    await _enter_intro(message, state)


@router.message(F.text.func(lambda text: norm_text(text) == norm_text(MENU_BUTTON)))
async def handle_intro_menu(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает кнопку возврата в меню смены из любого шага."""

    await safe_delete(message)
    await _return_to_menu(message, state)


@router.message(CrewState.INTRO, F.text.func(lambda text: norm_text(text) == norm_text(START_BUTTON)))
async def handle_intro_start(message: types.Message, state: FSMContext) -> None:
    """Переходит от интро к шагу выбора водителя."""

    await safe_delete(message)
    await _enter_driver_step(message, state)


@router.message(CrewState.DRIVER)
async def handle_driver_step(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает выбор водителя и навигацию шага 1."""

    await safe_delete(message)
    text = message.text or ""
    text_norm = norm_text(text)

    if text_norm == norm_text(MENU_BUTTON):
        return
    if text_norm == norm_text(BACK_BUTTON):
        await _enter_intro(message, state)
        return
    if text_norm == norm_text(ADD_DRIVER_BUTTON):
        await flash_message(message, "Добавление водителя пока недоступно.")
        await _enter_driver_step(message, state)
        return
    if text_norm == norm_text(NEXT_BUTTON):
        data = await state.get_data()
        driver_id = data.get("crew_driver_id")
        if isinstance(driver_id, int):
            await _enter_workers_step(message, state)
        else:
            await flash_message(message, "Сначала выберите водителя.")
            await _enter_driver_step(message, state)
        return

    data = await state.get_data()
    mapping: dict[str, int] = data.get("crew_map_buttons", {})
    choice = _resolve_choice(mapping, text)
    if choice is None:
        await flash_message(message, "Пожалуйста, используйте кнопки ниже.")
        await _enter_driver_step(message, state)
        return

    drivers = _deserialize_workers(data.get("crew_drivers"))
    driver_name = next((item.name for item in drivers if item.worker_id == choice), str(choice))
    await state.update_data(crew_driver_id=choice)
    await flash_message(message, f"✔ водитель выбран: {driver_name}")
    await _enter_driver_step(message, state)


@router.message(CrewState.WORKERS)
async def handle_workers_step(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает мультивыбор рабочих и подтверждение."""

    await safe_delete(message)
    text = message.text or ""
    text_norm = norm_text(text)

    if text_norm == norm_text(MENU_BUTTON):
        return
    if text_norm == norm_text(BACK_BUTTON):
        await _enter_driver_step(message, state)
        return
    if text_norm == norm_text(CLEAR_WORKERS_BUTTON):
        await state.update_data(crew_selected_worker_ids=[])
        await flash_message(message, "Список рабочих очищен.")
        await _enter_workers_step(message, state)
        return
    if text_norm == norm_text(CONFIRM_BUTTON):
        data = await state.get_data()
        selected_ids = _selected_ids(data)
        if not selected_ids:
            await flash_message(message, "Нужно выбрать хотя бы одного рабочего.")
            await _enter_workers_step(message, state)
            return
        await _save_and_finish(message, state)
        return

    data = await state.get_data()
    mapping: dict[str, int] = data.get("crew_map_buttons", {})
    choice = _resolve_choice(mapping, text)
    if choice is None:
        await flash_message(message, "Пожалуйста, используйте кнопки ниже.")
        await _enter_workers_step(message, state)
        return

    selected_set = set(_selected_ids(data))
    workers = _deserialize_workers(data.get("crew_workers"))
    worker_name = next((item.name for item in workers if item.worker_id == choice), str(choice))

    if choice in selected_set:
        selected_set.remove(choice)
        await flash_message(message, f"✖ удалён {worker_name}")
    else:
        selected_set.add(choice)
        await flash_message(message, f"✔ добавлен {worker_name}")

    await state.update_data(crew_selected_worker_ids=sorted(selected_set))
    await _enter_workers_step(message, state)
