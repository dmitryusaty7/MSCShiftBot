import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.handlers import registration
from bot.handlers.registration import RegistrationState
from bot.keyboards.auth import (
    CONFIRM_PAYLOAD,
    RETRY_PAYLOAD,
    SKIP_PAYLOAD,
    START_PAYLOAD,
    confirm_retry_kb,
    skip_button_kb,
    start_registration_kb,
)
from bot.validators.name import validate_name


class StubBot:
    """Минимальный бот для тестирования сообщений и обратных вызовов."""

    def __init__(self) -> None:
        self._counter = 1
        self.sent_messages: list[DummyMessage] = []
        self.deleted: list[tuple[int, int]] = []
        self.edited: list[tuple[int, int, object]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> "DummyMessage":
        message = DummyMessage(
            bot=self,
            user_id=0,
            chat_id=chat_id,
            message_id=self._counter,
            text=text,
            reply_markup=kwargs.get("reply_markup"),
            parse_mode=kwargs.get("parse_mode"),
        )
        self._counter += 1
        self.sent_messages.append(message)
        return message

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    async def edit_message_reply_markup(self, chat_id: int, message_id: int, reply_markup=None) -> None:
        self.edited.append((chat_id, message_id, reply_markup))


class DummyMessage:
    """Сообщение для передачи в хендлеры aiogram."""

    def __init__(
        self,
        *,
        bot: StubBot,
        user_id: int,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup=None,
        parse_mode: str | None = None,
    ) -> None:
        self.bot = bot
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = message_id
        self.text = text
        self.reply_markup = reply_markup
        self.parse_mode = parse_mode

    async def answer(self, text: str, reply_markup=None, parse_mode: str | None = None) -> "DummyMessage":
        return await self.bot.send_message(
            self.chat.id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async def edit_reply_markup(self, reply_markup=None) -> None:
        await self.bot.edit_message_reply_markup(self.chat.id, self.message_id, reply_markup=reply_markup)


class DummyCallbackQuery:
    """Фиктивный callback-query для проверки inline-навигации."""

    def __init__(self, *, bot: StubBot, message: DummyMessage, user_id: int, data: str) -> None:
        self.bot = bot
        self.message = message
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self._answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self._answers.append((text, show_alert))


class StubFSMContext:
    """Упрощённая реализация FSMContext для тестов."""

    def __init__(self) -> None:
        self._state: str | None = None
        self._data: dict[str, object] = {}

    async def set_state(self, value) -> None:  # noqa: ANN001 - интерфейс повторяет aiogram
        if value is None:
            self._state = None
        else:
            self._state = getattr(value, "state", value)

    async def get_state(self) -> str | None:
        return self._state

    async def clear(self) -> None:
        self._state = None
        self._data.clear()

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def get_data(self) -> dict[str, object]:
        return dict(self._data)


class StubSheetsService:
    """Заглушка сервиса таблиц с предсказуемым поведением."""

    def __init__(self) -> None:
        self.saved: list[tuple[int, str, str, str]] = []
        self.existing_row: tuple[int | None, str | None] = (None, None)
        self.duplicate = False

    def find_row_by_telegram_id(self, spreadsheet_id: str, telegram_id: int) -> tuple[int | None, str | None]:
        return self.existing_row

    def fio_duplicate_exists(self, spreadsheet_id: str, last: str, first: str, middle: str) -> bool:
        return self.duplicate

    def upsert_registration_row(
        self,
        spreadsheet_id: str,
        telegram_id: int,
        last: str,
        first: str,
        middle: str,
    ) -> int:
        self.saved.append((telegram_id, last, first, middle))
        return 42


@pytest.fixture(autouse=True)
def _ensure_env(monkeypatch):
    monkeypatch.setenv("SPREADSHEET_ID", "test-sheet")
    registration._SERVICE = None
    yield
    registration._SERVICE = None


@pytest.fixture
def stub_service(monkeypatch) -> StubSheetsService:
    service = StubSheetsService()
    registration._SERVICE = service

    async def fake_show_menu(message, service: StubSheetsService, state: StubFSMContext):  # noqa: ANN001
        fake_show_menu.calls.append((message, service, await state.get_state()))

    fake_show_menu.calls: list[tuple[DummyMessage, StubSheetsService, str | None]] = []
    monkeypatch.setattr(registration, "show_menu", fake_show_menu)
    return service


def test_validate_name_success():
    assert validate_name("иванов") == "Иванов"
    assert validate_name("  петров-иван  ") == "Петров-Иван"


def test_validate_name_failure():
    with pytest.raises(ValueError):
        validate_name("иванов1")
    with pytest.raises(ValueError):
        validate_name(" ")


def test_keyboards_structure():
    start_kb = start_registration_kb()
    assert start_kb.inline_keyboard[0][0].callback_data == START_PAYLOAD

    skip_kb = skip_button_kb()
    assert skip_kb.inline_keyboard[0][0].callback_data == SKIP_PAYLOAD

    confirm_kb = confirm_retry_kb()
    assert confirm_kb.inline_keyboard[0][0].callback_data == CONFIRM_PAYLOAD
    assert confirm_kb.inline_keyboard[0][1].callback_data == RETRY_PAYLOAD


async def _successful_registration_flow(stub_service: StubSheetsService) -> None:
    bot = StubBot()
    state = StubFSMContext()
    user_id = 101
    chat_id = 555
    incoming = DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=500, text="/start")

    await registration.handle_start(incoming, state)
    assert await state.get_state() == RegistrationState.start.state
    start_message = bot.sent_messages[-1]
    assert start_message.text == "Чтобы начать регистрацию, нажмите кнопку ниже."

    callback_start = DummyCallbackQuery(bot=bot, message=start_message, user_id=user_id, data=START_PAYLOAD)
    await registration.start_registration(callback_start, state)
    assert await state.get_state() == RegistrationState.last_name.state
    last_prompt = bot.sent_messages[-1]
    assert last_prompt.text == "Введите вашу Фамилию (только буквы)."

    await registration.process_last_name(
        DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=501, text="иванов"),
        state,
    )
    assert await state.get_state() == RegistrationState.first_name.state
    first_prompt = bot.sent_messages[-1]
    assert first_prompt.text == "Введите ваше Имя (только буквы)."

    await registration.process_first_name(
        DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=502, text="иван"),
        state,
    )
    assert await state.get_state() == RegistrationState.patronymic.state
    middle_prompt = bot.sent_messages[-1]
    assert middle_prompt.text.startswith("Введите ваше Отчество")

    await registration.process_patronymic(
        DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=503, text="иванович"),
        state,
    )
    assert await state.get_state() == RegistrationState.confirm.state
    confirm_message = bot.sent_messages[-1]
    assert "<b>Иванов Иван Иванович</b>" in confirm_message.text

    callback_confirm = DummyCallbackQuery(
        bot=bot, message=confirm_message, user_id=user_id, data=CONFIRM_PAYLOAD
    )
    await registration.confirm_registration(callback_confirm, state)

    assert stub_service.saved == [(user_id, "Иванов", "Иван", "Иванович")]
    assert any(msg.text == "Регистрация завершена. Статус: Активен. Открываю панель." for msg in bot.sent_messages)
    assert await state.get_state() is None
    assert bot.edited  # клавиатура подтверждения удалена
    assert registration.show_menu.calls  # type: ignore[attr-defined]


def test_successful_registration_flow(stub_service: StubSheetsService):
    asyncio.run(_successful_registration_flow(stub_service))


async def _existing_user_shortcuts_to_menu(stub_service: StubSheetsService) -> None:
    stub_service.existing_row = (10, "Активен")
    bot = StubBot()
    state = StubFSMContext()
    message = DummyMessage(bot=bot, user_id=77, chat_id=77, message_id=600, text="/start")

    await registration.handle_start(message, state)
    assert bot.sent_messages[0].text == "Добро пожаловать! Вы уже зарегистрированы. Открываю панель."
    assert registration.show_menu.calls  # type: ignore[attr-defined]
    assert await state.get_state() is None


def test_existing_user_shortcuts_to_menu(stub_service: StubSheetsService):
    asyncio.run(_existing_user_shortcuts_to_menu(stub_service))


async def _invalid_last_name_shows_and_clears_error(stub_service: StubSheetsService) -> None:
    bot = StubBot()
    state = StubFSMContext()
    user_id = 12
    chat_id = 77
    start_msg = DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=700, text="/start")
    await registration.handle_start(start_msg, state)
    callback_start = DummyCallbackQuery(
        bot=bot, message=bot.sent_messages[-1], user_id=user_id, data=START_PAYLOAD
    )
    await registration.start_registration(callback_start, state)

    await registration.process_last_name(
        DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=701, text="иванов1"),
        state,
    )
    error_message = bot.sent_messages[-1]
    assert error_message.text.startswith("Некорректная фамилия")

    await registration.process_last_name(
        DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=702, text="иванов"),
        state,
    )
    assert any(deleted_id == error_message.message_id for (_, deleted_id) in bot.deleted)
    assert await state.get_state() == RegistrationState.first_name.state


def test_invalid_last_name_shows_and_clears_error(stub_service: StubSheetsService):
    asyncio.run(_invalid_last_name_shows_and_clears_error(stub_service))


async def _skip_patronymic_uses_button(stub_service: StubSheetsService) -> None:
    bot = StubBot()
    state = StubFSMContext()
    user_id = 5
    chat_id = 5
    await registration.handle_start(
        DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=800, text="/start"),
        state,
    )
    await registration.start_registration(
        DummyCallbackQuery(bot=bot, message=bot.sent_messages[-1], user_id=user_id, data=START_PAYLOAD),
        state,
    )
    await registration.process_last_name(
        DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=801, text="иванов"),
        state,
    )
    await registration.process_first_name(
        DummyMessage(bot=bot, user_id=user_id, chat_id=chat_id, message_id=802, text="иван"),
        state,
    )
    prompt = bot.sent_messages[-1]
    await registration.skip_patronymic(
        DummyCallbackQuery(bot=bot, message=prompt, user_id=user_id, data=SKIP_PAYLOAD),
        state,
    )
    confirm_message = bot.sent_messages[-1]
    assert "<b>Иванов Иван</b>" in confirm_message.text
    assert any(item[1] == prompt.message_id for item in bot.deleted)
    assert await state.get_state() == RegistrationState.confirm.state


def test_skip_patronymic_uses_button(stub_service: StubSheetsService):
    asyncio.run(_skip_patronymic_uses_button(stub_service))
