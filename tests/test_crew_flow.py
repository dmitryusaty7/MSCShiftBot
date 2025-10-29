"""Тесты сценария раздела «Бригада» с Reply-клавиатурами."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.handlers import crew, shift_menu
from bot.handlers.crew import CrewState
from bot.keyboards.crew_reply import (
    CONFIRM_BUTTON,
    MENU_BUTTON,
    NEXT_BUTTON,
    START_BUTTON,
    make_driver_kb,
    make_intro_kb,
    make_workers_kb,
)
from bot.keyboards.crew_inline import WORKER_TOGGLE_PREFIX, make_workers_inline_summary
from bot.services import CrewWorker
from bot.utils.cleanup import reset_history
from bot.utils.textnorm import norm_text


def _flatten(markup: ReplyKeyboardMarkup) -> list[str]:
    return [
        button.text
        for row in markup.keyboard
        for button in row
        if isinstance(button, KeyboardButton)
    ]


def test_intro_keyboard_contains_start_and_menu() -> None:
    markup = make_intro_kb()
    texts = _flatten(markup)
    assert texts == [START_BUTTON, MENU_BUTTON]


def test_norm_text_handles_variations() -> None:
    variant = "\u2009▶️\uFE0F  Начать   заполнение"
    assert norm_text(variant) == norm_text(START_BUTTON)
    assert norm_text(" " + START_BUTTON + " ") == norm_text(START_BUTTON)


def test_driver_keyboard_returns_mapping() -> None:
    drivers = [CrewWorker(worker_id=i, name=f"Водитель {i}") for i in range(1, 4)]
    markup, mapping = make_driver_kb(drivers, driver_id=2)
    texts = _flatten(markup)
    assert any(text.startswith("✔") for text in texts)
    assert NEXT_BUTTON in texts
    assert mapping
    assert mapping[next(iter(mapping))] == 1


def test_workers_keyboard_shows_confirm_only_with_selection() -> None:
    workers = [CrewWorker(worker_id=i, name=f"Рабочий {i}") for i in range(1, 4)]
    markup_empty, _ = make_workers_kb(workers, [])
    texts_empty = _flatten(markup_empty)
    assert CONFIRM_BUTTON not in texts_empty

    markup_full, _ = make_workers_kb(workers, [1, 3])
    texts_full = _flatten(markup_full)
    assert CONFIRM_BUTTON in texts_full


def test_inline_summary_contains_toggle_buttons() -> None:
    driver = CrewWorker(worker_id=1, name="Иванов И.")
    workers = [
        CrewWorker(worker_id=1, name="Рабочий А"),
        CrewWorker(worker_id=2, name="Рабочий Б"),
    ]

    text, markup = make_workers_inline_summary(driver, workers)
    assert "Иванов" in text
    assert "Рабочий А" in text
    assert markup is not None

    buttons = [
        button
        for row in markup.inline_keyboard
        for button in row
    ]

    assert all(button.callback_data.startswith(WORKER_TOGGLE_PREFIX) for button in buttons)
    assert any(button.text.startswith("✖") for button in buttons)

    _, empty_markup = make_workers_inline_summary(driver, [])
    assert empty_markup is None


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
        self.chat = SimpleNamespace(id=chat_id, type="private")
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

    async def answer(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        return None


class StubFSMContext:
    def __init__(self) -> None:
        self._state: str | None = None
        self._data: dict[str, Any] = {}

    async def set_state(self, value) -> None:  # noqa: ANN001
        self._state = getattr(value, "state", value)

    async def get_state(self) -> str | None:
        return self._state

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def update_data(self, **kwargs: Any) -> None:
        self._data.update(kwargs)

    async def clear(self) -> None:
        self._state = None
        self._data.clear()


async def _run_flow(monkeypatch) -> tuple[StubCrewService, StubBot]:
    chat_id = 10
    user_id = 42
    reset_history(chat_id)

    service = StubCrewService(
        row=5,
        drivers=[CrewWorker(worker_id=1, name="Иванов И."), CrewWorker(worker_id=2, name="Петров П.")],
        workers=[
            CrewWorker(worker_id=1, name="Рабочий А"),
            CrewWorker(worker_id=2, name="Рабочий Б"),
        ],
    )

    original_service = crew._service
    crew._service = service

    original_flash = crew.flash_message

    async def fake_flash(target, text, ttl=2.0, **kwargs):  # noqa: ANN001
        base = getattr(target, "message", target)
        if base is None:
            return None
        return await base.answer(text)

    bot = StubBot()
    state = StubFSMContext()

    async def fake_render_menu(message, user_id, row, **kwargs):  # noqa: ANN001
        await message.answer("Меню смены\n👥 Состав бригады — ✅ готово")

    monkeypatch.setattr(shift_menu, "render_shift_menu", fake_render_menu)
    monkeypatch.setattr(crew, "flash_message", fake_flash)

    try:
        start_message = DummyMessage(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_id=bot.allocate_id(),
            text="/crew",
        )

        await crew.start_crew(start_message, state, user_id=user_id)
        assert await state.get_state() == CrewState.INTRO.state

        intro_message = DummyMessage(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_id=bot.allocate_id(),
            text=START_BUTTON,
        )
        await crew.handle_intro_start(intro_message, state)
        assert await state.get_state() == CrewState.DRIVER.state

        data = await state.get_data()
        driver_button = next(text for text, value in data["crew_map_buttons"].items() if value == 1)
        driver_message = DummyMessage(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_id=bot.allocate_id(),
            text=driver_button,
        )
        await crew.handle_driver_step(driver_message, state)
        data = await state.get_data()
        assert data.get("crew_driver_id") == 1

        next_message = DummyMessage(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_id=bot.allocate_id(),
            text=NEXT_BUTTON,
        )
        await crew.handle_driver_step(next_message, state)
        assert await state.get_state() == CrewState.WORKERS.state

        data = await state.get_data()
        worker_button = next(text for text, value in data["crew_map_buttons"].items() if value == 2)
        worker_message = DummyMessage(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_id=bot.allocate_id(),
            text=worker_button,
        )
        await crew.handle_workers_step(worker_message, state)
        data = await state.get_data()
        assert data.get("crew_selected_worker_ids") == [2]

        summary_id = data.get("crew_list_msg_id")
        assert isinstance(summary_id, int)
        summary_message = bot._storage[(chat_id, summary_id)]

        callback_remove = DummyCallback(summary_message, f"{WORKER_TOGGLE_PREFIX}2")
        await crew.handle_workers_inline(callback_remove, state)
        data = await state.get_data()
        assert data.get("crew_selected_worker_ids") == []

        summary_message = bot._storage[(chat_id, summary_id)]
        callback_add = DummyCallback(summary_message, f"{WORKER_TOGGLE_PREFIX}2")
        await crew.handle_workers_inline(callback_add, state)
        data = await state.get_data()
        assert data.get("crew_selected_worker_ids") == [2]

        confirm_message = DummyMessage(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_id=bot.allocate_id(),
            text=CONFIRM_BUTTON,
        )
        await crew.handle_workers_step(confirm_message, state)

        assert service.saved_calls
        assert await state.get_state() is None

        return service, bot
    finally:
        crew._service = original_service
        crew.flash_message = original_flash


def test_full_reply_flow(monkeypatch) -> None:
    service, bot = asyncio.run(_run_flow(monkeypatch))
    saved = service.saved_calls[-1]
    assert saved["driver"] == "Иванов И."
    assert saved["workers"] == ["Рабочий Б"]
    assert any("Меню смены" in (msg.text or "") for msg in bot.sent_messages)


async def _run_menu_from_intro(monkeypatch) -> bool:
    chat_id = 20
    user_id = 77
    reset_history(chat_id)

    service = StubCrewService(
        row=3,
        drivers=[CrewWorker(worker_id=1, name="Иванов И.")],
        workers=[CrewWorker(worker_id=1, name="Рабочий А")],
    )

    original_service = crew._service
    crew._service = service

    bot = StubBot()
    state = StubFSMContext()
    called = False

    async def fake_render_menu(message, user_id, row, **kwargs):  # noqa: ANN001
        nonlocal called
        called = True
        await message.answer("Меню смены")

    monkeypatch.setattr(shift_menu, "render_shift_menu", fake_render_menu)

    try:
        start_message = DummyMessage(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_id=bot.allocate_id(),
            text="/crew",
        )

        await crew.start_crew(start_message, state, user_id=user_id)

        menu_message = DummyMessage(
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            message_id=bot.allocate_id(),
            text=MENU_BUTTON,
        )

        await crew.handle_intro_menu(menu_message, state)

        return called
    finally:
        crew._service = original_service


def test_menu_button_returns_to_shift_menu(monkeypatch) -> None:
    called = asyncio.run(_run_menu_from_intro(monkeypatch))
    assert called
