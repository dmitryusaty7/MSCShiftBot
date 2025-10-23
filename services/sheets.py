from __future__ import annotations

"""Сервис-обёртка для работы с Google Sheets листом «Данные».

Реализация следует правилам, описанным в задаче по регистрации:
- поиск и проверка пользователя по Telegram ID (колонка A);
- запись регистрационных данных в диапазон A:G;
- проверка дублей по ФИО среди активных пользователей;
- нормализация Ф/И/О (капитализация первой буквы каждого сегмента);
- блокировка регистрации, если статус «Архив».
"""

import os
import re
from typing import Optional, Tuple

import gspread
from google.oauth2.service_account import Credentials

DATA_SHEET_NAME = os.getenv("DATA_SHEET_NAME", "Данные")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё\- ]{1,50}$")


def _titlecase(value: str) -> str:
    """Нормализация регистра для каждой части слова."""

    parts = [part.title() for part in re.split(r"(\s|-)", value.strip())]
    return "".join(parts)


def validate_name_piece(raw_value: str) -> Tuple[bool, str]:
    """Проверка и нормализация отдельного элемента ФИО."""

    if not raw_value or not _NAME_RE.fullmatch(raw_value.strip()):
        return False, "Только буквы, пробелы и дефис."
    return True, _titlecase(raw_value)


def get_client() -> gspread.Client:
    """Создаёт gspread-клиент по данным сервисного аккаунта."""

    json_path = (
        os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_PATH")
        or os.environ.get("SERVICE_ACCOUNT_JSON_PATH")
    )
    if not json_path:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON_PATH env required")

    credentials = Credentials.from_service_account_file(json_path, scopes=SCOPES)
    return gspread.authorize(credentials)


class SheetsService:
    """Обёртка вокруг gspread для операций регистрации."""

    def __init__(self) -> None:
        self.client = get_client()

    def ws(self, spreadsheet_id: str, sheet_name: str = DATA_SHEET_NAME) -> gspread.Worksheet:
        """Возвращает рабочий лист."""

        spreadsheet = self.client.open_by_key(spreadsheet_id)
        return spreadsheet.worksheet(sheet_name)

    # ---------- Регистрация на листе «Данные» ----------

    def find_row_by_telegram_id(
        self, spreadsheet_id: str, telegram_id: int
    ) -> Tuple[Optional[int], Optional[str]]:
        """Возвращает (row_index, status) по Telegram ID или (None, None)."""

        worksheet = self.ws(spreadsheet_id)
        values = worksheet.get("A2:G")
        for index, row in enumerate(values, start=2):
            telegram_cell = (row[0] or "").strip() if len(row) >= 1 else ""
            status_cell = (row[6] or "").strip() if len(row) >= 7 else ""
            if str(telegram_id) == telegram_cell:
                return index, status_cell or None
        return None, None

    def find_first_free_row_by_a(self, spreadsheet_id: str) -> int:
        """Возвращает первую свободную строку по колонке A."""

        worksheet = self.ws(spreadsheet_id)
        column_a = worksheet.col_values(1)
        if len(column_a) >= 1 and column_a[0]:
            return len(column_a) + 1
        return 2

    def fio_duplicate_exists(self, spreadsheet_id: str, last: str, first: str, middle: str) -> bool:
        """Проверяет наличие дубля по ФИО среди активных пользователей."""

        worksheet = self.ws(spreadsheet_id)
        values = worksheet.get("A2:G")
        for row in values:
            last_name = (row[1] or "").strip() if len(row) > 1 else ""
            first_name = (row[2] or "").strip() if len(row) > 2 else ""
            middle_name = (row[3] or "").strip() if len(row) > 3 else ""
            status = (row[6] or "").strip() if len(row) > 6 else ""
            if status == "Активен" and last_name == last and first_name == first and middle_name == middle:
                return True
        return False

    def upsert_registration_row(
        self,
        spreadsheet_id: str,
        telegram_id: int,
        last: str,
        first: str,
        middle: str,
    ) -> int:
        """Создаёт или возвращает строку регистрации пользователя."""

        worksheet = self.ws(spreadsheet_id)
        found_row, status = self.find_row_by_telegram_id(spreadsheet_id, telegram_id)
        if found_row:
            if status == "Архив":
                raise PermissionError("Пользователь помечён как Архив.")
            return found_row

        row_index = self.find_first_free_row_by_a(spreadsheet_id)
        worksheet.update([[""] * 7], f"A{row_index}:G{row_index}")
        worksheet.update(
            [[str(telegram_id), last, first, middle, "", "", "Активен"]],
            f"A{row_index}:G{row_index}",
        )
        return row_index
