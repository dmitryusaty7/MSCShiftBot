import asyncio
import logging

import pytest
from aiogram.exceptions import TelegramForbiddenError

from bot.handlers import shift_close
from bot.handlers import shift_menu
from bot.handlers.shift_menu import ShiftSession, ShiftState


class DummyBot:
    """Простой бот-заменитель для тестов уведомлений."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:  # pragma: no cover - kwargs не используются
        self.sent.append((chat_id, text))


class FailingBot:
    """Бот, имитирующий ошибку при отправке сообщения."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def send_message(self, *args, **kwargs) -> None:  # pragma: no cover - поведение фиксированное
        raise self._exc


def test_format_group_report_escapes_html() -> None:
    """HTML-отчёт экранирует пользовательские данные и добавляет ссылку на фото."""

    summary = {
        "date": "2024-03-15",
        "ship": "СПК <Восток>",
        "expenses": {"driver": 1000, "brigadier": 2000, "workers": 1500, "total": 4500},
        "materials": {"pvd_rolls_m": 120, "pvc_tubes": 15, "tape": 4, "photos_link": "https://disk"},
        "crew": {"driver": "Иван", "workers": ["Пётр", "<Анна>"]},
    }
    context = shift_close._compose_notification_context(
        "Бригадир <Test>",
        summary,
        shift_date="2024-03-15",
    )
    text = shift_close._format_group_report(context)
    assert "Бригадир &lt;Test&gt;" in text
    assert "СПК &lt;Восток&gt;" in text
    assert "(<a href='https://disk'>фото</a>)" in text
    assert len(text) < 4096


def test_compose_context_formats_date_from_sheet() -> None:
    """Дата из таблицы преобразуется к формату ДД.ММ.ГГГГ."""

    summary = {
        "date": "2025-10-29",
        "ship": "Арго",
        "expenses": {"total": 0},
        "materials": {},
        "crew": {},
    }
    context = shift_close._compose_notification_context(
        "Чистов П. О.",
        summary,
        shift_date="2025-10-30",
    )
    assert context.date == "30.10.2025"


def test_group_report_shows_dash_without_link() -> None:
    """При отсутствии ссылки выводится тире без HTML-якоря."""

    context = shift_close.GroupNotificationContext(
        date="01.01.2025",
        user="Бригадир",
        vessel="Арго",
        expenses_total="1 000 ₽",
        materials_summary="—",
        materials_link=None,
        crew_summary="—",
    )
    text = shift_close._format_group_report(context)
    assert "📦 Материалы: —" in text
    assert "<a href" not in text


def test_notify_group_without_chat_id(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """При отсутствии chat_id уведомление не отправляется и логируется предупреждение."""

    shift_close._last_notified.clear()
    monkeypatch.delenv("GROUP_CHAT_ID", raising=False)
    ctx = shift_close.GroupNotificationContext(
        date="2024-03-15",
        user="Бригадир",
        vessel="СПК",
        expenses_total="100 ₽",
        materials_summary="—",
        materials_link=None,
        crew_summary="—",
    )
    bot = DummyBot()
    caplog.set_level(logging.WARNING)
    asyncio.run(shift_close._notify_group(bot, ctx, row=11))
    assert "не указан" in caplog.text
    assert 11 in shift_close._last_notified
    assert bot.sent == []


def test_notify_group_invalid_chat_id(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Некорректный chat_id приводил бы к предупреждению без исключения."""

    shift_close._last_notified.clear()
    monkeypatch.setenv("GROUP_CHAT_ID", "oops")
    ctx = shift_close.GroupNotificationContext(
        date="2024-03-15",
        user="Бригадир",
        vessel="СПК",
        expenses_total="100 ₽",
        materials_summary="—",
        materials_link=None,
        crew_summary="—",
    )
    bot = DummyBot()
    caplog.set_level(logging.WARNING)
    asyncio.run(shift_close._notify_group(bot, ctx, row=12))
    assert "некорректен" in caplog.text
    assert 12 in shift_close._last_notified


def test_notify_group_logs_send_failure(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Ошибки отправки в чат логируются, но не прерывают сценарий."""

    shift_close._last_notified.clear()
    monkeypatch.setenv("GROUP_CHAT_ID", "-1001234567890")
    ctx = shift_close.GroupNotificationContext(
        date="2024-03-15",
        user="Бригадир",
        vessel="СПК",
        expenses_total="100 ₽",
        materials_summary="—",
        materials_link=None,
        crew_summary="—",
    )
    error = TelegramForbiddenError(method="send_message", message="forbidden")
    bot = FailingBot(error)
    caplog.set_level(logging.WARNING)
    asyncio.run(shift_close._notify_group(bot, ctx, row=13))
    assert "Не удалось отправить уведомление" in caplog.text
    assert 13 in shift_close._last_notified


def test_mark_shift_closed_skips_finalize() -> None:
    """Закрытие смены не вызывает запись в таблицу."""

    class StubBase:
        def finalize_shift(self, *args, **kwargs):  # pragma: no cover - вызов запрещён
            raise AssertionError("finalize_shift не должен вызываться")

        def is_shift_closed(self, row: int) -> bool:
            return False

    base = StubBase()
    service = shift_close.ShiftCloseSheetsService(base=base)

    assert service.mark_shift_closed(row=7, user_id=101) is True
    assert service.mark_shift_closed(row=7, user_id=101) is False


def test_handle_shift_close_request_success_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешное закрытие смены не изменяет таблицы и отправляет уведомление."""

    class StubBaseService:
        def __init__(self) -> None:
            self.progress_requests: list[tuple[int, int]] = []
            self.summary_rows: list[int] = []
            self.date_rows: list[int] = []
            self.profile_requests: list[int] = []

        def get_shift_progress(self, user_id: int, row: int) -> dict[str, bool]:
            self.progress_requests.append((user_id, row))
            return {"expenses": True, "materials": True, "crew": True}

        def get_shift_summary(self, row: int) -> dict[str, object]:
            self.summary_rows.append(row)
            return {
                "date": "2025-10-29",
                "ship": "Арго",
                "expenses": {
                    "driver": 1000,
                    "brigadier": 2000,
                    "workers": 1580,
                    "aux": 0,
                    "food": 0,
                    "taxi": 0,
                    "other": 0,
                },
                "materials": {
                    "pvd_rolls_m": 0,
                    "pvc_tubes": 0,
                    "tape": 0,
                    "photos_link": "https://disk.example",
                },
                "crew": {
                    "brigadier": "Чистов П. О.",
                    "driver": "Иванов И. И.",
                    "workers": ["Гринёв И. П."],
                },
            }

        def get_shift_date(self, row: int) -> str:
            self.date_rows.append(row)
            return "2025-10-30"

        def is_shift_closed(self, row: int) -> bool:
            return False

        def get_user_profile(self, user_id: int):  # pragma: no cover - не требуется
            self.profile_requests.append(user_id)
            return None

    class StubState:
        def __init__(self) -> None:
            self.set_calls: list[object] = []
            self.update_calls: list[dict[str, object]] = []

        async def set_state(self, value: object) -> None:
            self.set_calls.append(value)

        async def update_data(self, **kwargs: object) -> None:
            self.update_calls.append(kwargs)

    class StubMessage:
        def __init__(self, bot: DummyBot, user_id: int) -> None:
            self.bot = bot
            self.from_user = type(
                "User",
                (),
                {"id": user_id, "full_name": "Чистов П. О.", "username": "chistov"},
            )()
            self.chat = type("Chat", (), {"id": 999})()
            self.message_id = 1
            self.text = shift_close.FINISH_SHIFT_BUTTON
            self.answers: list[str] = []

        async def answer(self, text: str, **kwargs: object) -> None:  # pragma: no cover - не используется
            self.answers.append(text)

    base = StubBaseService()
    service = shift_close.ShiftCloseSheetsService(base=base)
    monkeypatch.setattr(shift_close, "_service", service, raising=False)
    monkeypatch.setattr(shift_close, "_get_service", lambda: service)

    flash_calls: list[tuple[str, float]] = []

    async def fake_flash(message, text: str, ttl: float = 1.0):  # type: ignore[override]
        flash_calls.append((text, ttl))
        return None

    cleanup_calls: list[tuple[object, bool]] = []

    async def fake_cleanup(message, state, keep_start: bool = False):  # type: ignore[override]
        cleanup_calls.append((message, keep_start))

    render_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_render(*args, **kwargs):  # type: ignore[override]
        render_calls.append((args, kwargs))

    notifications: list[tuple[shift_close.GroupNotificationContext, int]] = []

    async def fake_notify(bot, ctx, *, row):  # type: ignore[override]
        notifications.append((ctx, row))

    dashboard_calls: list[tuple[object, object, object]] = []

    async def fake_show_dashboard(message, state=None, service=None):
        dashboard_calls.append((message, state, service))

    monkeypatch.setattr(shift_close, "flash_message", fake_flash)
    monkeypatch.setattr(shift_close, "cleanup_after_confirm", fake_cleanup)
    monkeypatch.setattr(shift_close, "render_shift_menu", fake_render)
    monkeypatch.setattr(shift_close, "_notify_group", fake_notify)
    monkeypatch.setattr("bot.handlers.dashboard.show_dashboard", fake_show_dashboard)

    user_id = 321
    row = 15
    monkeypatch.setattr(
        shift_menu,
        "_sessions",
        {user_id: ShiftSession("2025-10-30", row, {"expenses": True, "materials": True, "crew": True}, False)},
    )

    message = StubMessage(DummyBot(), user_id)
    state = StubState()

    asyncio.run(shift_close.handle_shift_close_request(message, state))

    assert flash_calls[0][0] == "💾 Сохраняю…"
    assert any(text == "Сохраняю данные…" for text, _ in flash_calls)
    assert flash_calls[-1][0] == "✅ Смена закрыта."
    assert cleanup_calls and cleanup_calls[0][1] is False
    assert not render_calls
    assert dashboard_calls and dashboard_calls[0][2] is base
    assert notifications and notifications[0][1] == row
    ctx = notifications[0][0]
    assert ctx.date == "30.10.2025"
    report = shift_close._format_group_report(ctx)
    assert '<a href="https://disk.example">фото</a>' in report or "<a href='https://disk.example'>фото</a>" in report
    assert shift_menu.get_shift_session(user_id).closed is True
    assert state.set_calls == [ShiftState.IDLE]
    assert state.update_calls == [{"shift_close_row": None}]
    assert base.progress_requests == [(user_id, row)]
    assert base.summary_rows == [row]
    assert base.date_rows == [row]
