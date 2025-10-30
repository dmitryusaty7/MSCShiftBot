import asyncio
import logging

import pytest
from aiogram.exceptions import TelegramForbiddenError

from bot.handlers import shift_close


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
    """HTML-отчёт экранирует пользовательские данные и не превышает лимиты."""

    summary = {
        "date": "2024-03-15",
        "ship": "СПК <Восток>",
        "expenses": {"driver": 1000, "brigadier": 2000, "workers": 1500, "total": 4500},
        "materials": {"pvd_rolls_m": 120, "pvc_tubes": 15, "tape": 4, "photos_link": "https://disk"},
        "crew": {"driver": "Иван", "workers": ["Пётр", "<Анна>"]},
    }
    context = shift_close._compose_notification_context("Бригадир <Test>", summary)
    text = shift_close._format_group_report(context)
    assert "Бригадир &lt;Test&gt;" in text
    assert "СПК &lt;Восток&gt;" in text
    assert len(text) < 4096


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
        crew_summary="—",
    )
    error = TelegramForbiddenError(method="send_message", message="forbidden")
    bot = FailingBot(error)
    caplog.set_level(logging.WARNING)
    asyncio.run(shift_close._notify_group(bot, ctx, row=13))
    assert "Не удалось отправить уведомление" in caplog.text
    assert 13 in shift_close._last_notified
