"""Сервис-обёртка для работы с Google Sheets листом «Данные».

В модуле собраны все операции чтения и записи, используемые при регистрации
пользователей. Основные требования:

* поиск и обновление записей строго по ``telegram_id``;
* запись ведётся только через ``Worksheet.update`` без ``append_row``;
* блокировка пользователей со статусом «Архив»;
* нормализация и проверка корректности элементов ФИО.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable, Optional, Tuple, TypeVar

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

from services.env import require_env

logger = logging.getLogger(__name__)

DATA_SHEET_NAME = os.getenv("DATA_SHEET_NAME", "Данные")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё\- ]{1,50}$")
T = TypeVar("T")


def retry(func: Callable[[], T], tries: int = 3, backoff: float = 0.5) -> T:
    """Повторяет сетевой вызов при временных ошибках Google API."""

    attempt = 1
    while True:
        try:
            return func()
        except APIError as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            is_retryable = status in {429} or (isinstance(status, int) and 500 <= status < 600)
            if not is_retryable:
                logger.error("Ошибка Google API без повтора: %s", error)
                raise

            if attempt >= tries:
                logger.error("Предел попыток исчерпан (%s): %s", tries, error)
                raise

            logger.warning(
                "Повтор %s/%s после ошибки %s", attempt, tries, status or "без кода"
            )
            time.sleep(backoff * attempt)
            attempt += 1


def _norm_tid(value: object) -> str:
    """Приводит Telegram ID к строке из цифр без пробелов и суффикса «.0»."""

    if value is None:
        return ""

    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits


def normalize_name_piece(value: str) -> str:
    """Приводит часть ФИО к нормализованному виду с сохранением разделителей."""

    value = (value or "").strip()
    if not value:
        return ""

    parts = re.split(r"(\s+|-)", value)
    normalized = []
    for part in parts:
        if part == "":
            continue
        if part.isspace() or part == "-":
            normalized.append(part)
        else:
            normalized.append(part.title())
    return "".join(normalized)


def validate_name_piece(raw_value: str) -> str:
    """Проверяет часть ФИО и возвращает нормализованное значение."""

    candidate = (raw_value or "").strip()
    if not _NAME_RE.fullmatch(candidate):
        raise ValueError("Допустимы только буквы, пробелы и дефис (1-50 символов).")
    return normalize_name_piece(candidate)


def get_client() -> gspread.Client:
    """Создаёт gspread-клиент по данным сервисного аккаунта."""

    json_path = require_env("GOOGLE_SERVICE_ACCOUNT_JSON_PATH")
    credentials = Credentials.from_service_account_file(json_path, scopes=SCOPES)
    return gspread.authorize(credentials)


class SheetsService:
    """Обёртка вокруг gspread для операций регистрации."""

    def __init__(self) -> None:
        self.client = get_client()

    def ws(self, spreadsheet_id: str, sheet_name: str = DATA_SHEET_NAME) -> gspread.Worksheet:
        """Возвращает рабочий лист."""

        def _get_ws() -> gspread.Worksheet:
            spreadsheet = self.client.open_by_key(spreadsheet_id)
            return spreadsheet.worksheet(sheet_name)

        worksheet = retry(_get_ws)
        logger.info("Открыт лист %s (таблица %s)", worksheet.title, spreadsheet_id)
        return worksheet

    # ---------- Регистрация на листе «Данные» ----------

    def find_row_by_telegram_id(
        self, spreadsheet_id: str, telegram_id: int
    ) -> Tuple[Optional[int], Optional[str]]:
        """Возвращает (row_index, status) по Telegram ID или (None, None)."""

        worksheet = self.ws(spreadsheet_id)
        logger.info(
            "Чтение диапазона %s!A2:G для поиска пользователя %s",
            worksheet.title,
            telegram_id,
        )
        values = retry(lambda: worksheet.get("A2:G"))
        target = _norm_tid(telegram_id)
        for index, row in enumerate(values, start=2):
            telegram_cell = _norm_tid(row[0] if len(row) >= 1 else "")
            status_cell = (row[6] or "").strip() if len(row) >= 7 else ""
            if telegram_cell and telegram_cell == target:
                return index, status_cell or None
        return None, None

    def find_first_free_row_by_A(self, spreadsheet_id: str) -> int:
        """Возвращает первую свободную строку по колонке A."""

        worksheet = self.ws(spreadsheet_id)
        logger.info("Получение длины колонки A на листе %s", worksheet.title)
        column_a = retry(lambda: worksheet.col_values(1))
        next_row = max(len(column_a) + 1, 2)
        logger.info("Первая свободная строка: %s", next_row)
        return next_row

    def fio_duplicate_exists(self, spreadsheet_id: str, last: str, first: str, middle: str) -> bool:
        """Проверяет наличие дубля по ФИО среди активных пользователей."""

        worksheet = self.ws(spreadsheet_id)
        logger.info(
            "Чтение диапазона %s!A2:G для проверки дублей ФИО", worksheet.title
        )
        values = retry(lambda: worksheet.get("A2:G"))
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

            logger.info(
                "Обновление данных пользователя %s в строке %s", telegram_id, found_row
            )
            retry(lambda: worksheet.update([["", "", ""]], f"B{found_row}:D{found_row}"))
            retry(
                lambda: worksheet.update(
                    [[last, first, middle]], f"B{found_row}:D{found_row}"
                )
            )
            retry(lambda: worksheet.update([["Активен"]], f"G{found_row}:G{found_row}"))
            return found_row

        row_index = self.find_first_free_row_by_A(spreadsheet_id)
        logger.info(
            "Создание новой строки %s для пользователя %s", row_index, telegram_id
        )
        retry(lambda: worksheet.update([["", "", "", "", "", "", ""]], f"A{row_index}:G{row_index}"))
        retry(
            lambda: worksheet.update(
                [[
                    _norm_tid(telegram_id),
                    last,
                    first,
                    middle,
                    "",
                    "",
                    "Активен",
                ]],
                f"A{row_index}:G{row_index}",
            )
        )
        return row_index
