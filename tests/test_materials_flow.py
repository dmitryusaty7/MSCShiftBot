"""Юнит-тесты клавиатур и защитных механизмов раздела «Материалы»."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.keyboards.materials import (
    CONFIRM_BUTTON,
    DELETE_LAST_BUTTON,
    MENU_BUTTON,
    START_MATERIALS_BUTTON,
    materials_photos_keyboard,
    materials_start_keyboard,
)
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
