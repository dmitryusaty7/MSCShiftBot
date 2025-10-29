"""Юнит-тесты клавиатур и защитных механизмов раздела «Материалы»."""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.handlers import materials
from bot.handlers.materials import MaterialsState
from bot.handlers.shift_menu import reset_shift_session
from bot.keyboards.materials import (
    CONFIRM_BUTTON,
    DELETE_LAST_BUTTON,
    EDIT_BUTTON,
    MENU_BUTTON,
    START_MATERIALS_BUTTON,
    materials_confirm_keyboard,
    materials_photos_keyboard,
    materials_start_keyboard,
)
from bot.utils.cleanup import reset_history
from services.sheets import MAT_COL_PVD_INCOMING, _build_materials_updates


def _flatten(markup: ReplyKeyboardMarkup) -> list[str]:
    """Возвращает список подписей всех кнопок."""

    return [button.text for row in markup.keyboard for button in row if isinstance(button, KeyboardButton)]


def test_start_keyboard_contains_start_and_menu() -> None:
    """Стартовая клавиатура материалов содержит запуск и возврат."""

    markup = materials_start_keyboard()
    texts = _flatten(markup)
    assert START_MATERIALS_BUTTON in texts
    assert MENU_BUTTON in texts
    assert len(texts) == 2


def test_photos_keyboard_contains_controls() -> None:
    """Клавиатура управления фото содержит подтверждение и удаление."""

    markup = materials_photos_keyboard()
    texts = _flatten(markup)
    assert CONFIRM_BUTTON in texts
    assert DELETE_LAST_BUTTON in texts
    assert MENU_BUTTON in texts


def test_confirm_keyboard_contains_confirm_and_edit() -> None:
    """Экран подтверждения материалов предлагает подтверждение и редактирование."""

    markup = materials_confirm_keyboard()
    texts = _flatten(markup)
    assert CONFIRM_BUTTON in texts
    assert EDIT_BUTTON in texts
    assert MENU_BUTTON in texts


def test_build_materials_updates_preserves_income_column() -> None:
    """При обновлении других полей значение колонки E сохраняется."""

    updates = _build_materials_updates(
        worksheet_title="Материалы",
        row=7,
        pvd_income="125",
        pvd_m=10,
        pvc_pcs=None,
        tape_pcs=5,
        folder_link="https://example.com",
    )
    ranges = {entry["range"]: entry["values"][0][0] for entry in updates}
    target_range = f"Материалы!{MAT_COL_PVD_INCOMING}7"
    assert target_range in ranges
    assert ranges[target_range] == "125"


def test_build_materials_updates_skips_empty_income() -> None:
    """Если поступление не задано, колонка E не попадает в обновления."""

    updates = _build_materials_updates(
        worksheet_title="Материалы",
        row=3,
        pvd_income="   ",
        pvd_m=None,
        pvc_pcs=None,
        tape_pcs=None,
        folder_link=None,
    )
    ranges = {entry["range"] for entry in updates}
    assert f"Материалы!{MAT_COL_PVD_INCOMING}3" not in ranges


class StubSheetsService:
    """Заглушка сервиса таблиц для интеграционного теста материалов."""

    def __init__(self, row: int = 5) -> None:
        self.row = row
        self.materials_saved = False
        self.saved_calls: list[tuple[int, int, int, int, str | None]] = []

    def get_shift_row_index_for_user(self, telegram_id: int) -> int | None:  # noqa: D401 - упрощённая заглушка
        return self.row

    def open_shift_for_user(self, telegram_id: int) -> int:
        return self.row

    def save_materials_block(
        self,
        row: int,
        *,
        pvd_m: int,
        pvc_pcs: int,
        tape_pcs: int,
        folder_link: str,
    ) -> None:
        self.saved_calls.append((row, pvd_m, pvc_pcs, tape_pcs, folder_link))
        self.materials_saved = True

    def get_shift_progress(self, user_id: int, row: int) -> dict[str, bool]:
        return {"expenses": False, "materials": self.materials_saved, "crew": False}

    def is_shift_closed(self, row: int) -> bool:
        return False

    def get_shift_date(self, row: int) -> str:
        return "2024-01-02"


class StubDrive:
    """Минимальная заглушка хранилища фото."""

    def __init__(self) -> None:
        self.folders: list[str] = []
        self.saved: list[tuple[str, str, str, bytes]] = []

    def get_or_create_daily_folder(self, title: str) -> None:
        self.folders.append(title)

    def save_photo(
        self,
        content: bytes,
        filename: str,
        folder: str,
        *,
        content_type: str,
    ) -> None:
        self.saved.append((filename, folder, content_type, content))

    def folder_public_link(self, folder: str) -> str:
        return f"https://disk.example/{folder}"


class StubBot:
    """Упрощённая реализация методов бота Telegram."""

    def __init__(self) -> None:
        self.sent_messages: list[DummyMessage] = []
        self.deleted: list[tuple[int, int]] = []
        self._counter = 1

    def allocate_id(self) -> int:
        current = self._counter
        self._counter += 1
        return current

    async def send_message(self, chat_id: int, text: str, **kwargs) -> "DummyMessage":
        message = DummyMessage(
            bot=self,
            chat_id=chat_id,
            user_id=0,
            message_id=self.allocate_id(),
            text=text,
            reply_markup=kwargs.get("reply_markup"),
        )
        self.sent_messages.append(message)
        return message

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    async def get_file(self, file_id: str) -> SimpleNamespace:
        return SimpleNamespace(file_path=f"{file_id}.jpg")

    async def download_file(self, file_path: str) -> bytes:
        return b"binary-photo"


class DummyMessage:
    """Сообщение, которым оперируют обработчики aiogram."""

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

    async def answer(
        self,
        text: str,
        reply_markup=None,
        parse_mode: str | None = None,
        **kwargs,
    ) -> "DummyMessage":
        return await self.bot.send_message(
            self.chat.id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async def delete(self) -> None:
        await self.bot.delete_message(self.chat.id, self.message_id)


class StubFSMContext:
    """Простая реализация FSMContext для пошаговых тестов."""

    def __init__(self) -> None:
        self._state: str | None = None
        self._data: dict[str, object] = {}

    async def set_state(self, value) -> None:  # noqa: ANN001 - совместимость с aiogram
        if value is None:
            self._state = None
        else:
            self._state = getattr(value, "state", value)

    async def get_state(self) -> str | None:
        return self._state

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def get_data(self) -> dict[str, object]:
        return dict(self._data)


def _make_user_message(
    bot: StubBot,
    *,
    chat_id: int,
    user_id: int,
    text: str | None,
    photo: list[SimpleNamespace] | None = None,
) -> DummyMessage:
    message = DummyMessage(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        message_id=bot.allocate_id(),
        text=text,
    )
    if photo is not None:
        message.photo = photo
    message.date = dt.datetime.now()
    return message


async def _run_materials_flow(monkeypatch) -> None:
    chat_id = 777
    user_id = 42

    reset_history(chat_id)
    reset_shift_session(user_id)

    service = StubSheetsService(row=11)
    drive = StubDrive()
    bot = StubBot()
    state = StubFSMContext()

    trigger = DummyMessage(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        message_id=bot.allocate_id(),
        text="📦 Материалы — ✍️ заполнить",
    )

    materials._service = None
    materials._drive = None
    monkeypatch.setattr(materials, "_get_service", lambda: service)
    monkeypatch.setattr(materials, "_get_drive", lambda: drive)

    await materials.start_materials(trigger, state, user_id=user_id)

    assert await state.get_state() == MaterialsState.INTRO.state
    intro_message = bot.sent_messages[-1]
    assert "Материалы — ввод данных" in (intro_message.text or "")
    assert intro_message.reply_markup is not None

    start_input = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text=START_MATERIALS_BUTTON)
    await materials.handle_intro(start_input, state)
    assert await state.get_state() == MaterialsState.PVD.state

    pvd_prompt = bot.sent_messages[-1]
    assert "ПВД" in (pvd_prompt.text or "")

    pvd_input = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text="12")
    await materials.handle_pvd(pvd_input, state)
    assert await state.get_state() == MaterialsState.PVC.state

    pvc_input = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text="3")
    await materials.handle_pvc(pvc_input, state)
    assert await state.get_state() == MaterialsState.TAPE.state

    tape_input = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text="Пропустить")
    await materials.handle_tape(tape_input, state)
    assert await state.get_state() == MaterialsState.PHOTOS.state

    photo_input = _make_user_message(
        bot,
        chat_id=chat_id,
        user_id=user_id,
        text=None,
        photo=[SimpleNamespace(file_id="photo-file")],
    )
    await materials.handle_photo(photo_input, state)

    confirm_photos = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text=CONFIRM_BUTTON)
    await materials.handle_photos_controls(confirm_photos, state)
    assert await state.get_state() == MaterialsState.CONFIRM.state

    confirm_summary = _make_user_message(bot, chat_id=chat_id, user_id=user_id, text=CONFIRM_BUTTON)
    await materials.handle_confirm(confirm_summary, state)

    assert service.saved_calls
    row, pvd_m, pvc_pcs, tape_pcs, folder_link = service.saved_calls[-1]
    assert row == 11
    assert pvd_m == 12
    assert pvc_pcs == 3
    assert tape_pcs == 0
    assert folder_link.startswith("https://disk.example/")

    assert drive.saved
    saved_entry = drive.saved[-1]
    expected_day = dt.datetime.now().astimezone().date().strftime("%Y-%m-%d")
    assert saved_entry[1] == expected_day

    final_state = await state.get_state()
    assert final_state == "ShiftState:ACTIVE"

    menu_message = bot.sent_messages[-1]
    assert "📦 Материалы — ✅ готово" in (menu_message.text or "")
    assert menu_message.reply_markup is not None

    done_ids = [msg.message_id for msg in bot.sent_messages if msg.text == "Раздел «материалы» сохранён ✅"]
    if done_ids:
        assert any(message_id == deleted_id for (_, deleted_id) in bot.deleted for message_id in done_ids)


def test_materials_flow_success(monkeypatch) -> None:
    """Полный сценарий раздела «Материалы» до возврата в меню смены."""

    asyncio.run(_run_materials_flow(monkeypatch))
