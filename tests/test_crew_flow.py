"""Тесты нового сценария раздела «Бригада»."""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.handlers import crew
from bot.handlers.crew import CrewState
from bot.handlers.shift_menu import reset_shift_session
from bot.keyboards.crew_inline import make_list_kb
from bot.keyboards.crew_reply import (
    ADD_WORKER_BUTTON,
    BACK_BUTTON,
    CLEAR_WORKERS_BUTTON,
    CONFIRM_BUTTON,
    EDIT_BUTTON,
    crew_confirm_keyboard,
    crew_start_keyboard,
)
from bot.services import CrewWorker
from bot.utils.cleanup import reset_history


def _flatten(markup: ReplyKeyboardMarkup) -> list[str]:
    return [
        button.text
        for row in markup.keyboard
        for button in row
        if isinstance(button, KeyboardButton)
    ]


def test_start_keyboard_contains_controls() -> None:
    markup = crew_start_keyboard()
    texts = _flatten(markup)
    assert ADD_WORKER_BUTTON in texts
    assert CLEAR_WORKERS_BUTTON in texts
    assert CONFIRM_BUTTON in texts
    assert BACK_BUTTON in texts
    assert len(texts) == 5  # четыре действия + переход в панель


def test_confirm_keyboard_contains_confirm_and_edit() -> None:
    markup = crew_confirm_keyboard()
    texts = _flatten(markup)
    assert texts == [CONFIRM_BUTTON, EDIT_BUTTON]


def test_inline_keyboard_uses_worker_ids() -> None:
    workers = [CrewWorker(worker_id=7, name="Иванов И."), CrewWorker(worker_id=9, name="Петров П.")]
    markup = make_list_kb(workers)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == ["crew:rm:7", "crew:rm:9"]


class StubCrewService:
    def __init__(self, *, row: int, driver: str, workers: list[CrewWorker]) -> None:
        self.row = row
        self.driver = driver
        self.workers = workers
        self.saved_calls: list[dict[str, Any]] = []
        self.saved = False

    def get_shift_row_index_for_user(self, telegram_id: int) -> int | None:  # noqa: D401 - упрощённая заглушка
        return self.row

    def open_shift_for_user(self, telegram_id: int) -> int:
        return self.row

    def list_active_workers(self) -> list[CrewWorker]:
        return list(self.workers)

    def get_shift_summary(self, row: int) -> dict[str, Any]:
        return {
            "crew": {
                "driver": self.driver,
                "workers": [],
            }
        }

    def save_crew(self, row: int, *, driver: str, workers: list[str], telegram_id: int | None = None) -> None:
        self.saved = True
        self.saved_calls.append(
            {
                "row": row,
                "driver": driver,
                "workers": list(workers),
                "telegram_id": telegram_id,
            }
        )

    # Методы для рендеринга меню смены ---------------------------------
    def get_shift_progress(self, user_id: int, row: int) -> dict[str, bool]:  # noqa: D401 - упрощённая заглушка
        return {"expenses": False, "materials": False, "crew": self.saved}

    def is_shift_closed(self, row: int) -> bool:  # noqa: D401 - упрощённая заглушка
        return False

    def get_shift_date(self, row: int) -> str:  # noqa: D401 - упрощённая заглушка
        return dt.date.today().isoformat()

    def base_service(self) -> "StubCrewService":  # type: ignore[override]
        return self


class StubBot:
    def __init__(self) -> None:
        self.sent_messages: list[DummyMessage] = []
        self.deleted: list[tuple[int, int]] = []
        self.edited: list[tuple[int, int, str]] = []
        self._counter = 1
        self._storage: dict[tuple[int, int], DummyMessage] = {}

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
        key = (chat_id, message_id)
        message = self._storage.get(key)
        if message is None:
            raise TelegramBadRequest(method="editMessageText", message="Message to edit not found")
        message.text = text
        message.reply_markup = reply_markup
        self.edited.append((chat_id, message_id, text))

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
        self.from_user = SimpleNamespace(id=user_id)
        self.message_id = message_id
        self.text = text
        self.reply_markup = reply_markup
        self.photo: list[SimpleNamespace] = []
        self.date = dt.datetime.now()

    async def answer(self, text: str, reply_markup=None, parse_mode: str | None = None, **kwargs: Any) -> "DummyMessage":
        return await self.bot.send_message(self.chat.id, text, reply_markup=reply_markup)

    async def delete(self) -> None:
        await self.bot.delete_message(self.chat.id, self.message_id)


class StubFSMContext:
    def __init__(self) -> None:
        self._state: str | None = None
        self._data: dict[str, Any] = {}

    async def set_state(self, value) -> None:  # noqa: ANN001 - совместимость с aiogram
        if value is None:
            self._state = None
        else:
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


def _make_user_message(bot: StubBot, *, chat_id: int, user_id: int, text: str) -> DummyMessage:
    return DummyMessage(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        message_id=bot.allocate_id(),
        text=text,
    )


async def _run_crew_flow(monkeypatch) -> None:
    chat_id = 700
    user_id = 55

    reset_history(chat_id)
    reset_shift_session(user_id)

    workers = [
        CrewWorker(worker_id=1, name="Иванов И."),
        CrewWorker(worker_id=2, name="Петров П."),
    ]
    service = StubCrewService(row=8, driver="Сидоров С.", workers=workers)
    bot = StubBot()
    state = StubFSMContext()

    trigger = DummyMessage(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        message_id=bot.allocate_id(),
        text="👥 Состав бригады — ✍️ заполнить",
    )

    crew._service = None
    monkeypatch.setattr(crew, "_get_service", lambda: service)

    from bot.handlers import shift_menu

    shift_menu._service = None
    monkeypatch.setattr(shift_menu, "_get_service", lambda: service.base_service())

    await crew.start_crew(trigger, state, user_id=user_id)

    assert await state.get_state() == CrewState.WORKERS.state

    data = await state.get_data()
    context = data.get("crew_ctx", {})
    workers_msg_id = context.get("workers_message_id")
    screen_msg_id = context.get("screen_message_id")
    assert isinstance(workers_msg_id, int) and workers_msg_id > 0
    assert isinstance(screen_msg_id, int) and screen_msg_id > 0

    inline_message = bot._storage[(chat_id, workers_msg_id)]
    assert inline_message.text == "выбранные рабочие:\n—"

    add_msg = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text=ADD_WORKER_BUTTON)
    await crew.handle_workers_menu(add_msg, state)
    assert await state.get_state() == CrewState.AWAIT_WORKER.state

    prompt_message = bot.sent_messages[-1]
    assert "Укажите номер рабочего" in (prompt_message.text or "")

    select_msg = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text="1")
    await crew.handle_worker_number(select_msg, state)

    assert await state.get_state() == CrewState.WORKERS.state

    inline_message = bot._storage[(chat_id, workers_msg_id)]
    assert "1) Иванов И." in (inline_message.text or "")

    # prompt и пользовательский ввод должны быть удалены
    prompt_deleted = any(message_id == prompt_message.message_id for (_, message_id) in bot.deleted)
    assert prompt_deleted

    confirm_msg = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text=CONFIRM_BUTTON)
    await crew.handle_workers_menu(confirm_msg, state)
    assert await state.get_state() == CrewState.CONFIRM.state

    screen_message = bot._storage[(chat_id, screen_msg_id)]
    assert "Проверьте состав бригады" in (screen_message.text or "")
    assert "Иванов И." in (screen_message.text or "")

    final_msg = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text=CONFIRM_BUTTON)
    await crew.handle_confirm(final_msg, state)

    assert service.saved_calls
    saved = service.saved_calls[-1]
    assert saved["row"] == 8
    assert saved["driver"] == "Сидоров С."
    assert saved["workers"] == ["Иванов И."]
    assert saved["telegram_id"] == user_id

    final_state = await state.get_state()
    assert final_state == "ShiftState:ACTIVE"

    menu_message = bot.sent_messages[-1]
    assert "👥 Состав бригады — ✅ готово" in (menu_message.text or "")

    success_ids = [msg.message_id for msg in bot.sent_messages if msg.text == "Состав бригады сохранён ✅"]
    if success_ids:
        deleted_ids = {message_id for (_, message_id) in bot.deleted}
        assert success_ids[-1] in deleted_ids


def test_crew_flow_success(monkeypatch) -> None:
    asyncio.run(_run_crew_flow(monkeypatch))
