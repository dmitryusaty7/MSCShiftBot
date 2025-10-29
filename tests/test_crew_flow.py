"""Тесты обновлённого сценария раздела «Бригада» (водитель → рабочие)."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.handlers import crew
from bot.handlers.crew import CrewState
from bot.keyboards.crew_inline import (
    CONFIRM_CALLBACK,
    DRIVER_LIST_PREFIX,
    DRIVER_PICK_PREFIX,
    NOOP_CALLBACK,
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
from bot.services import CrewWorker
from bot.utils.cleanup import reset_history


def _flatten(markup: ReplyKeyboardMarkup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row if isinstance(button, KeyboardButton)]


def test_start_keyboard_contains_controls() -> None:
    markup = crew_start_keyboard()
    texts = _flatten(markup)
    assert {ADD_WORKER_BUTTON, CLEAR_WORKERS_BUTTON, CONFIRM_BUTTON, BACK_BUTTON, MENU_BUTTON} <= set(texts)
    assert len(texts) == 5


def test_confirm_keyboard_contains_confirm_and_edit() -> None:
    markup = crew_confirm_keyboard()
    texts = _flatten(markup)
    assert texts == [CONFIRM_BUTTON, EDIT_BUTTON]


def test_driver_keyboard_marks_selected_and_paginates() -> None:
    drivers = [CrewWorker(worker_id=i, name=f"Водитель {i}") for i in range(1, 9)]
    markup, page, total = build_driver_keyboard(drivers, page=0, selected_driver_id=5)
    assert total == 2
    assert page == 0
    buttons = [button for row in markup.inline_keyboard for button in row]
    texts = [button.text for button in buttons]
    assert any(text.startswith("✔") for text in texts)
    assert any(DRIVER_LIST_PREFIX in (button.callback_data or "") for button in buttons)


def test_worker_keyboard_disables_confirm_without_selection() -> None:
    workers = [CrewWorker(worker_id=i, name=f"Рабочий {i}") for i in range(1, 4)]
    markup_empty, _, _ = build_worker_keyboard(workers, page=0, selected_ids=[])
    last_row = markup_empty.inline_keyboard[-1]
    assert last_row[-1].callback_data == NOOP_CALLBACK

    markup_full, _, _ = build_worker_keyboard(workers, page=0, selected_ids=[1, 2])
    last_row_full = markup_full.inline_keyboard[-1]
    assert last_row_full[-1].callback_data == CONFIRM_CALLBACK


class StubCrewService:
    def __init__(self, *, row: int, drivers: list[CrewWorker], workers: list[CrewWorker]) -> None:
        self.row = row
        self.drivers = drivers
        self.workers = workers
        self.saved_calls: list[dict[str, Any]] = []

    def get_shift_row_index_for_user(self, telegram_id: int) -> int | None:
        return self.row

    def open_shift_for_user(self, telegram_id: int) -> int:
        return self.row

    def list_active_drivers(self) -> list[CrewWorker]:
        return list(self.drivers)

    def list_active_workers(self) -> list[CrewWorker]:
        return list(self.workers)

    def save_crew(self, row: int, *, driver: str, workers: list[str], telegram_id: int | None = None) -> None:
        self.saved_calls.append(
            {
                "row": row,
                "driver": driver,
                "workers": list(workers),
                "telegram_id": telegram_id,
            }
        )

    # Методы для рендеринга меню смены ---------------------------------
    def base_service(self) -> "StubCrewService":  # type: ignore[override]
        return self

    def get_shift_progress(self, user_id: int, row: int) -> dict[str, bool]:
        return {"crew": bool(self.saved_calls)}

    def is_shift_closed(self, row: int) -> bool:
        return False

    def get_shift_date(self, row: int) -> str:
        return dt.date.today().isoformat()


class StubBot:
    def __init__(self) -> None:
        self.sent_messages: list[DummyMessage] = []
        self.deleted: list[tuple[int, int]] = []
        self._storage: dict[tuple[int, int], DummyMessage] = {}
        self._counter = 1

    def allocate_id(self) -> int:
        current = self._counter
        self._counter += 1
        return current

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> "DummyMessage":
        message = DummyMessage(
            bot=self,
            chat_id=chat_id,
            user_id=0,
            message_id=self.allocate_id(),
            text=text,
            reply_markup=kwargs.get("reply_markup"),
        )
        self.sent_messages.append(message)
        self._storage[(chat_id, message.message_id)] = message
        return message

    async def edit_message_text(self, text: str, *, chat_id: int, message_id: int, reply_markup=None) -> None:
        message = self._storage.get((chat_id, message_id))
        if message is None:
            raise TelegramBadRequest(method="editMessageText", message="Message to edit not found")
        message.text = text
        message.reply_markup = reply_markup

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))
        self._storage.pop((chat_id, message_id), None)


class DummyMessage:
    def __init__(
        self,
        *,
        bot: StubBot,
        chat_id: int,
        user_id: int,
        message_id: int,
        text: str | None,
        reply_markup=None,
    ) -> None:
        self.bot = bot
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, is_bot=False)
        self.message_id = message_id
        self.text = text
        self.reply_markup = reply_markup
        self.date = dt.datetime.now()

    async def answer(self, text: str, reply_markup=None, **kwargs: Any) -> "DummyMessage":
        return await self.bot.send_message(self.chat.id, text, reply_markup=reply_markup)

    async def delete(self) -> None:
        await self.bot.delete_message(self.chat.id, self.message_id)


class DummyCallback:
    def __init__(self, message: DummyMessage, data: str) -> None:
        self.message = message
        self.data = data

    async def answer(self, *args: Any, **kwargs: Any) -> None:
        return None


class StubFSMContext:
    def __init__(self) -> None:
        self._state: str | None = None
        self._data: dict[str, Any] = {}

    async def set_state(self, value) -> None:  # noqa: ANN001
        self._state = getattr(value, "state", value)

    async def get_state(self) -> str | None:
        return self._state

    async def update_data(self, **kwargs: Any) -> None:
        self._data.update(kwargs)

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def clear(self) -> None:
        self._state = None
        self._data.clear()


async def _run_flow(monkeypatch) -> tuple[StubCrewService, StubBot]:
    chat_id = 500
    user_id = 42

    reset_history(chat_id)

    drivers = [CrewWorker(worker_id=1, name="Иванов И."), CrewWorker(worker_id=2, name="Петров П.")]
    workers = [
        CrewWorker(worker_id=1, name="Рабочий А"),
        CrewWorker(worker_id=2, name="Рабочий Б"),
        CrewWorker(worker_id=3, name="Рабочий В"),
    ]
    service = StubCrewService(row=7, drivers=drivers, workers=workers)
    bot = StubBot()
    state = StubFSMContext()

    trigger = DummyMessage(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        message_id=bot.allocate_id(),
        text="старт",
    )

    crew._service = None
    monkeypatch.setattr(crew, "_get_service", lambda: service)

    from bot.handlers import shift_menu

    async def fake_render_shift_menu(message, user_id, row, **kwargs):  # noqa: ANN001
        await message.bot.send_message(message.chat.id, f"Меню смены (row={row})")

    shift_menu._service = None
    monkeypatch.setattr(shift_menu, "_get_service", lambda: service.base_service())
    monkeypatch.setattr(shift_menu, "render_shift_menu", fake_render_shift_menu)

    await crew.start_crew(trigger, state, user_id=user_id)

    assert await state.get_state() == CrewState.DRIVER.state

    data = await state.get_data()
    inline_id = data.get("crew_inline_id")
    screen_id = data.get("crew_screen_id")
    assert isinstance(inline_id, int)
    assert isinstance(screen_id, int)

    inline_message = bot._storage[(chat_id, inline_id)]
    # Выбрать водителя
    callback_pick = DummyCallback(inline_message, f"{DRIVER_PICK_PREFIX}1")
    await crew.handle_driver_pick(callback_pick, state)

    # Перейти к списку рабочих
    callback_next = DummyCallback(inline_message, f"{WORKER_LIST_PREFIX}0")
    await crew.handle_worker_page(callback_next, state)
    assert await state.get_state() == CrewState.WORKERS.state

    # Выбрать рабочего
    inline_message = bot._storage[(chat_id, inline_id)]
    callback_worker = DummyCallback(inline_message, f"{WORKER_TOGGLE_PREFIX}2")
    await crew.handle_worker_toggle(callback_worker, state)

    data = await state.get_data()
    assert data.get("crew_selected_worker_ids") == [2]

    # Подтверждение через inline
    callback_confirm = DummyCallback(inline_message, CONFIRM_CALLBACK)
    await crew.handle_inline_confirm(callback_confirm, state)
    assert await state.get_state() == CrewState.CONFIRM.state

    # Финальное подтверждение
    final_message = DummyMessage(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        message_id=bot.allocate_id(),
        text=CONFIRM_BUTTON,
    )
    await crew.handle_confirm_save(final_message, state)

    assert service.saved_calls
    saved = service.saved_calls[-1]
    assert saved["row"] == 7
    assert saved["driver"] == "Иванов И."
    assert saved["workers"] == ["Рабочий Б"]
    assert saved["telegram_id"] == user_id

    return service, bot


def test_full_flow(monkeypatch) -> None:
    service, bot = asyncio.run(_run_flow(monkeypatch))
    assert service.saved_calls
    menu_messages = [msg.text for msg in bot.sent_messages if msg.text and "Меню смены" in msg.text]
    assert menu_messages
