"""Reply-сценарий раздела «Бригада» с интро, выбором водителя и рабочих."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Sequence

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.crew_inline import WORKER_TOGGLE_PREFIX, make_workers_inline_summary
from bot.keyboards.crew_reply import (
    ADD_DRIVER_BUTTON,
    BACK_BUTTON,
    CLEAR_WORKERS_BUTTON,
    CONFIRM_BUTTON,
    EDIT_BUTTON,
    MENU_BUTTON,
    NEXT_BUTTON,
    START_BUTTON,
    make_confirmation_kb,
    make_driver_kb,
    make_intro_kb,
    make_workers_kb,
)
from bot.services import CrewSheetsService, CrewWorker
from bot.utils.cleanup import remember_message, send_screen_message
from bot.utils.flash import flash_message
from bot.utils.textnorm import norm_text

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


def _norm(text: str | None) -> str:
    """Упрощённый алиас для нормализации текста кнопок."""

    return norm_text(text)


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

    can_edit = isinstance(screen_id, int) and (
        reply_markup is None or isinstance(reply_markup, types.InlineKeyboardMarkup)
    )

    if can_edit:
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
    target = _norm(text)
    for key, value in mapping.items():
        if _norm(key) == target:
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
    if workers:
        lines.extend(f"- {worker.name}" for worker in workers)
    else:
        lines.append("- —")
    lines.extend(["", "Сохранить изменения?"])
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


async def render_workers_inline_list(message: types.Message, state: FSMContext) -> None:
    """Отрисовывает (или обновляет) inline-сводку выбранных рабочих."""

    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    workers = _deserialize_workers(data.get("crew_workers"))

    driver_id = data.get("crew_driver_id") if isinstance(data.get("crew_driver_id"), int) else None
    selected_ids = set(_selected_ids(data))

    driver = next((item for item in drivers if item.worker_id == driver_id), None)
    selected_workers = [worker for worker in workers if worker.worker_id in selected_ids]

    text, markup = make_workers_inline_summary(driver, selected_workers)

    list_id = data.get("crew_list_msg_id")
    if isinstance(list_id, int):
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=list_id,
                reply_markup=markup,
            )
            return
        except TelegramBadRequest:
            logger.debug("Не удалось обновить сообщение сводки, отправляю новое.")
        except Exception:  # noqa: BLE001
            logger.warning("Ошибка при обновлении сводки", exc_info=True)

    summary = await message.answer(text, reply_markup=markup)
    remember_message(message.chat.id, summary.message_id)
    await state.update_data(crew_list_msg_id=summary.message_id)


async def _enter_intro(message: types.Message, state: FSMContext) -> None:
    await state.update_data(
        crew_map_buttons={},
        crew_confirmation_pending=False,
    )
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

    await state.update_data(
        crew_map_buttons=mapping,
        crew_confirmation_pending=False,
    )
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

    await state.update_data(
        crew_driver_name=driver.name if isinstance(driver, CrewWorker) else data.get("crew_driver_name"),
        crew_selected_worker_names=[worker.name for worker in selected_workers],
        crew_confirmation_pending=False,
    )

    markup, mapping = make_workers_kb(workers, selected_ids)

    await state.update_data(crew_map_buttons=mapping)
    await state.set_state(CrewState.WORKERS)
    await show_screen(
        message,
        state,
        text=_workers_step_text(driver, selected_workers),
        reply_markup=markup,
    )

    await render_workers_inline_list(message, state)


async def _show_confirmation(message: types.Message, state: FSMContext) -> None:
    """Показывает экран подтверждения перед сохранением."""

    data = await state.get_data()
    drivers = _deserialize_workers(data.get("crew_drivers"))
    workers = _deserialize_workers(data.get("crew_workers"))
    driver_id = data.get("crew_driver_id")
    selected_ids = _selected_ids(data)

    driver = next((item for item in drivers if item.worker_id == driver_id), None)
    if driver is None or not selected_ids:
        await flash_message(message, "Заполните все поля перед подтверждением.")
        await _enter_workers_step(message, state)
        return

    selected_workers = [worker for worker in workers if worker.worker_id in set(selected_ids)]

    await state.update_data(
        crew_selected_worker_names=[worker.name for worker in selected_workers],
        crew_confirmation_pending=True,
    )

    await show_screen(
        message,
        state,
        text=_summary_text(driver, selected_workers),
        reply_markup=make_confirmation_kb(),
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

    row = data.get("row")
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

    return row, drivers, workers


async def _return_to_menu(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data.get("crew_user_id")
    row = data.get("row")
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
    logger.debug("start_crew: chat=%s user=%s", message.chat.id, user_id)
    try:
        row, drivers, workers = await _prepare_references(state, user_id)
    except Exception:  # noqa: BLE001
        await message.answer("Не удалось открыть раздел «Бригада». Попробуйте позже.")
        await state.clear()
        return

    await state.update_data(
        crew_user_id=user_id,
        row=row,
        crew_driver_id=None,
        crew_driver_name=None,
        crew_selected_worker_ids=[],
        crew_selected_worker_names=[],
        crew_map_buttons={},
        crew_screen_id=None,
        crew_list_msg_id=None,
        crew_drivers=_serialize_workers(drivers),
        crew_workers=_serialize_workers(workers),
        crew_confirmation_pending=False,
    )

    await state.set_state(CrewState.INTRO)

    await _enter_intro(message, state)


@router.message(F.text.func(lambda text: _norm(text) == _norm(MENU_BUTTON)))
async def handle_intro_menu(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает кнопку возврата в меню смены из любого шага."""

    logger.debug(
        "handle_intro_menu: state=%s text=%r",
        await state.get_state(),
        message.text,
    )
    await _return_to_menu(message, state)


@router.message(CrewState.INTRO, F.text.func(lambda text: _norm(text) == _norm(START_BUTTON)))
async def handle_intro_start(message: types.Message, state: FSMContext) -> None:
    """Переходит от интро к шагу выбора водителя."""

    logger.debug(
        "handle_intro_start: state=%s text=%r",
        await state.get_state(),
        message.text,
    )
    await state.set_state(CrewState.DRIVER)
    await ask_driver(message, state)


@router.message(CrewState.DRIVER)
async def handle_driver_step(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает выбор водителя и навигацию шага 1."""

    logger.debug(
        "handle_driver_step: state=%s text=%r",
        await state.get_state(),
        message.text,
    )
    text = message.text or ""
    text_norm = _norm(text)

    if text_norm == _norm(MENU_BUTTON):
        await _return_to_menu(message, state)
        return
    if text_norm == _norm(BACK_BUTTON):
        await _enter_intro(message, state)
        return
    if text_norm == _norm(ADD_DRIVER_BUTTON):
        await flash_message(message, "Добавление водителя пока недоступно.")
        await _enter_driver_step(message, state)
        return
    if text_norm == _norm(NEXT_BUTTON):
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
    await state.update_data(crew_driver_id=choice, crew_driver_name=driver_name)
    await flash_message(message, f"✔ водитель выбран: {driver_name}")
    await _enter_driver_step(message, state)


@router.message(CrewState.WORKERS)
async def handle_workers_step(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает мультивыбор рабочих и подтверждение."""

    logger.debug(
        "handle_workers_step: state=%s text=%r",
        await state.get_state(),
        message.text,
    )
    text = message.text or ""
    text_norm = _norm(text)

    if text_norm == _norm(MENU_BUTTON):
        await state.update_data(crew_confirmation_pending=False)
        await _return_to_menu(message, state)
        return
    if text_norm == _norm(BACK_BUTTON):
        await state.update_data(crew_confirmation_pending=False)
        await _enter_driver_step(message, state)
        return
    if text_norm == _norm(CLEAR_WORKERS_BUTTON):
        await state.update_data(
            crew_selected_worker_ids=[],
            crew_selected_worker_names=[],
            crew_confirmation_pending=False,
        )
        await flash_message(message, "Список рабочих очищен.")
        await _enter_workers_step(message, state)
        return

    data = await state.get_data()
    confirmation_pending = bool(data.get("crew_confirmation_pending"))

    if confirmation_pending:
        if text_norm == _norm(CONFIRM_BUTTON):
            await _save_and_finish(message, state)
            return
        if text_norm == _norm(EDIT_BUTTON):
            await state.update_data(crew_confirmation_pending=False)
            await _enter_workers_step(message, state)
            return

    if text_norm == _norm(CONFIRM_BUTTON):
        await _show_confirmation(message, state)
        return

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
        await state.update_data(crew_confirmation_pending=False)
        await _enter_workers_step(message, state)
        return

    selected_set.add(choice)
    ordered_workers = [worker for worker in workers if worker.worker_id in selected_set]
    await state.update_data(
        crew_selected_worker_ids=[worker.worker_id for worker in ordered_workers],
        crew_selected_worker_names=[worker.name for worker in ordered_workers],
        crew_confirmation_pending=False,
    )
    await flash_message(message, f"✔ добавлен {worker_name}")
    await _enter_workers_step(message, state)


@router.callback_query(F.data.startswith(WORKER_TOGGLE_PREFIX))
async def handle_workers_inline(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает удаление/добавление рабочего из inline-сводки."""

    message = callback.message
    if message is None:
        await callback.answer()
        return

    logger.debug("handle_workers_inline: state=%s data=%r", await state.get_state(), callback.data)
    data = await state.get_data()
    raw = callback.data or ""
    try:
        worker_id = int(raw.split(":")[-1])
    except ValueError:
        await callback.answer()
        return

    selected_set = set(_selected_ids(data))
    workers = _deserialize_workers(data.get("crew_workers"))
    worker_name = next((item.name for item in workers if item.worker_id == worker_id), str(worker_id))

    if worker_id not in selected_set:
        await callback.answer()
        return

    selected_set.remove(worker_id)
    ordered_workers = [worker for worker in workers if worker.worker_id in selected_set]
    await state.update_data(
        crew_selected_worker_ids=[worker.worker_id for worker in ordered_workers],
        crew_selected_worker_names=[worker.name for worker in ordered_workers],
        crew_confirmation_pending=False,
    )
    await callback.answer()
    await flash_message(callback, f"✖ удалён {worker_name}", ttl=1.0)
    await _enter_workers_step(message, state)


# ---------------------------------------------------------------------------
# Совместимость с предыдущими версиями (обёртки публичных функций)
# ---------------------------------------------------------------------------


async def enter_driver_step(message: types.Message, state: FSMContext) -> None:
    """Совместимый алиас для PR #21."""

    await _enter_driver_step(message, state)


async def enter_workers_step(message: types.Message, state: FSMContext) -> None:
    """Совместимый алиас для PR #21."""

    await _enter_workers_step(message, state)


async def ask_driver(message: types.Message, state: FSMContext) -> None:
    """Алиас для совместимости с тестами, ожидающими ask_driver."""

    await _enter_driver_step(message, state)


__all__ = [
    "CrewState",
    "start_crew",
    "handle_intro_start",
    "handle_driver_step",
    "handle_workers_step",
    "handle_workers_inline",
    "handle_intro_menu",
    "enter_driver_step",
    "enter_workers_step",
    "ask_driver",
    "render_workers_inline_list",
]
