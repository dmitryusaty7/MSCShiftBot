"""Тесты клавиатур главной панели и меню смены."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.keyboards.dashboard import (
    FINISH_SHIFT_BUTTON,
    GUIDE_BUTTON,
    SHIFT_BACK_BUTTON,
    START_SHIFT_BUTTON,
    dashboard_keyboard,
    shift_menu_keyboard,
)


def _flatten_texts(markup: ReplyKeyboardMarkup) -> list[str]:
    """Возвращает список всех подписей кнопок из клавиатуры."""

    return [button.text for row in markup.keyboard for button in row if isinstance(button, KeyboardButton)]


def test_dashboard_keyboard_contains_start_button() -> None:
    """В клавиатуре главной панели всегда есть кнопка запуска смены."""

    markup = dashboard_keyboard()
    assert isinstance(markup, ReplyKeyboardMarkup)
    texts = _flatten_texts(markup)
    assert START_SHIFT_BUTTON in texts
    assert GUIDE_BUTTON not in texts


def test_dashboard_keyboard_with_guide() -> None:
    """При запросе дополнительной кнопки в клавиатуре появляется «Руководство»"""

    markup = dashboard_keyboard(include_guide=True)
    texts = _flatten_texts(markup)
    assert START_SHIFT_BUTTON in texts
    assert GUIDE_BUTTON in texts


def test_shift_menu_keyboard_without_finish() -> None:
    """Кнопка завершения не отображается до заполнения всех разделов."""

    markup = shift_menu_keyboard(
        expenses_done=False,
        materials_done=True,
        crew_done=False,
        show_finish=False,
    )
    texts = _flatten_texts(markup)
    assert FINISH_SHIFT_BUTTON not in texts
    assert texts.count(SHIFT_BACK_BUTTON) == 1


def test_shift_menu_keyboard_with_finish() -> None:
    """После заполнения всех разделов появляется кнопка завершения смены."""

    markup = shift_menu_keyboard(
        expenses_done=True,
        materials_done=True,
        crew_done=True,
        show_finish=True,
    )
    texts = _flatten_texts(markup)
    assert FINISH_SHIFT_BUTTON in texts
    assert texts.count(SHIFT_BACK_BUTTON) == 1
