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
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Optional, Tuple, TypeVar

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
SHEET_DATA = DATA_SHEET_NAME
SHEET_EXPENSES = "Расходы смены"
SHEET_MATERIALS = "Материалы"
SHEET_CREW = "Состав бригады"

EXPENSES_COL_DATE = "A"
EXPENSES_COL_TG = "B"

MATERIALS_COL_TG = "B"

CREW_COL_TG = "B"
CREW_COL_FIO = "E"

DATA_COL_TG = "A"
DATA_COL_FIO = "E"
DATA_COL_CLOSED_SHIFTS = "F"

# пользовательские столбцы, которые нужно заполнить вручную
EXPENSES_USER_COLS = [chr(code) for code in range(ord("C"), ord("L") + 1)]
MATERIALS_USER_COLS = ["A"] + [chr(code) for code in range(ord("C"), ord("N") + 1)]
CREW_USER_COLS = ["A", "C", "D", "F", "G"]

T = TypeVar("T")


@dataclass
class UserProfile:
    telegram_id: int
    fio: str
    closed_shifts: int
    fio_compact: str = ""


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


def _initial(value: str) -> str:
    """Возвращает первую букву строки для инициалов."""

    for char in value.strip():
        if char.isalpha():
            return char.upper()
    return ""


def format_compact_fio(last: str, first: str, middle: str) -> str:
    """Формирует короткую запись ФИО вида «Фамилия И. О.» или «Фамилия И.»."""

    pieces: list[str] = []
    last_clean = last.strip()
    if last_clean:
        pieces.append(last_clean)

    first_initial = _initial(first)
    if first_initial:
        pieces.append(f"{first_initial}.")

    middle_initial = _initial(middle)
    if middle_initial:
        pieces.append(f"{middle_initial}.")

    return " ".join(pieces).strip()


def format_display_name(first: str, middle: str) -> str:
    """Возвращает приветственное имя вида «Имя Отчество» или только «Имя»."""

    first_clean = first.strip()
    middle_clean = middle.strip()
    if first_clean and middle_clean:
        return f"{first_clean} {middle_clean}"
    if first_clean:
        return first_clean
    if middle_clean:
        return middle_clean
    return ""


def get_client() -> gspread.Client:
    """Создаёт gspread-клиент по данным сервисного аккаунта."""

    json_path = os.getenv("SERVICE_ACCOUNT_JSON_PATH") or os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON_PATH"
    )
    if not json_path:
        raise RuntimeError("SERVICE_ACCOUNT_JSON_PATH не задан")
    credentials = Credentials.from_service_account_file(json_path, scopes=SCOPES)
    return gspread.authorize(credentials)


class SheetsService:
    """Обёртка вокруг gspread для операций регистрации."""

    EXPENSES_USER_COLS = EXPENSES_USER_COLS
    MATERIALS_USER_COLS = MATERIALS_USER_COLS
    CREW_USER_COLS = CREW_USER_COLS

    def __init__(self) -> None:
        self.client = get_client()
        self._spreadsheet_cache: dict[str, gspread.Spreadsheet] = {}
        self._worksheet_cache: dict[tuple[str, str], gspread.Worksheet] = {}

    def ws(self, spreadsheet_id: str, sheet_name: str = DATA_SHEET_NAME) -> gspread.Worksheet:
        """Возвращает рабочий лист."""

        worksheet = self._get_worksheet(sheet_name, spreadsheet_id)
        logger.info("Открыт лист %s (таблица %s)", worksheet.title, spreadsheet_id)
        return worksheet

    def _get_spreadsheet(self, spreadsheet_id: Optional[str] = None) -> gspread.Spreadsheet:
        """Возвращает объект таблицы с кэшированием."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        if sid not in self._spreadsheet_cache:
            self._spreadsheet_cache[sid] = retry(lambda: self.client.open_by_key(sid))
        return self._spreadsheet_cache[sid]

    def _get_worksheet(
        self, sheet_name: str, spreadsheet_id: Optional[str] = None
    ) -> gspread.Worksheet:
        """Возвращает рабочий лист с кэшированием."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        cache_key = (sid, sheet_name)
        if cache_key not in self._worksheet_cache:
            spreadsheet = self._get_spreadsheet(sid)
            self._worksheet_cache[cache_key] = retry(
                lambda: spreadsheet.worksheet(sheet_name)
            )
        return self._worksheet_cache[cache_key]

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
        target_full = " ".join(part for part in (last, first, middle) if part)
        for row in values:
            last_name = (row[1] or "").strip() if len(row) > 1 else ""
            first_name = (row[2] or "").strip() if len(row) > 2 else ""
            middle_name = (row[3] or "").strip() if len(row) > 3 else ""
            fio_cell = (row[4] or "").strip() if len(row) > 4 else ""
            status = (row[6] or "").strip() if len(row) > 6 else ""
            if status != "Активен":
                continue

            if last_name == last and first_name == first and middle_name == middle:
                return True

            existing_full = " ".join(
                part for part in (last_name, first_name, middle_name) if part
            )
            if not existing_full:
                existing_full = fio_cell

            if existing_full and target_full and existing_full.casefold() == target_full.casefold():
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
        spreadsheet = self._get_spreadsheet(spreadsheet_id)
        sheet_name = worksheet.title
        fio_full = " ".join(part for part in (last, first, middle) if part)

        def build_updates(row_index: int) -> list[dict[str, object]]:
            updates: list[dict[str, object]] = [
                {
                    "range": f"{sheet_name}!A{row_index}",
                    "values": [[_norm_tid(telegram_id)]],
                },
                {
                    "range": f"{sheet_name}!B{row_index}:C{row_index}",
                    "values": [[last, first]],
                },
                {
                    "range": f"{sheet_name}!G{row_index}",
                    "values": [["Активен"]],
                },
            ]

            if fio_full:
                try:
                    fio_cell = retry(
                        lambda: worksheet.cell(
                            row_index,
                            self._col_to_index(DATA_COL_FIO),
                            value_render_option="FORMULA",
                        )
                    )
                    raw_value = getattr(fio_cell, "value", "")
                except Exception:  # noqa: BLE001
                    raw_value = ""

                if not (isinstance(raw_value, str) and raw_value.startswith("=")):
                    processed_fio = format_compact_fio(last, first, middle)
                    if processed_fio:
                        updates.append(
                            {
                                "range": f"{sheet_name}!{DATA_COL_FIO}{row_index}",
                                "values": [[processed_fio]],
                            }
                        )

            if middle:
                try:
                    middle_cell = retry(
                        lambda: worksheet.cell(
                            row_index,
                            self._col_to_index("D"),
                            value_render_option="FORMULA",
                        )
                    )
                    middle_raw = getattr(middle_cell, "value", "")
                except Exception:  # noqa: BLE001
                    middle_raw = ""

                if not (isinstance(middle_raw, str) and middle_raw.startswith("=")):
                    updates.append(
                        {
                            "range": f"{sheet_name}!D{row_index}",
                            "values": [[middle]],
                        }
                    )

            return updates

        found_row, status = self.find_row_by_telegram_id(spreadsheet_id, telegram_id)
        if found_row:
            if status == "Архив":
                raise PermissionError("Пользователь помечён как Архив.")

            logger.info(
                "Обновление данных пользователя %s в строке %s", telegram_id, found_row
            )
            updates = build_updates(found_row)
            retry(
                lambda: spreadsheet.values_batch_update(
                    {"valueInputOption": "USER_ENTERED", "data": updates}
                )
            )
            return found_row

        row_index = self.find_first_free_row_by_A(spreadsheet_id)
        logger.info(
            "Создание новой строки %s для пользователя %s", row_index, telegram_id
        )
        updates = build_updates(row_index)
        retry(
            lambda: spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": updates}
            )
        )
        return row_index


    # ---------- Работа с рабочими листами смен ----------

    def get_user_profile(
        self, telegram_id: int, spreadsheet_id: Optional[str] = None
    ) -> UserProfile:
        """Возвращает профиль пользователя из листа «Данные» по Telegram ID."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_data = self._get_worksheet(SHEET_DATA, sid)
        row = self._find_user_row_in_data(ws_data, telegram_id)
        if not row:
            raise RuntimeError("пользователь не найден в листе «Данные»")

        first_cell = retry(lambda: ws_data.acell(f"C{row}"))
        middle_cell = retry(lambda: ws_data.acell(f"D{row}"))
        fio_cell = retry(lambda: ws_data.acell(f"{DATA_COL_FIO}{row}"))
        closed_cell = retry(lambda: ws_data.acell(f"{DATA_COL_CLOSED_SHIFTS}{row}"))

        first = (first_cell.value or "").strip()
        middle = (middle_cell.value or "").strip()
        fio_compact = (fio_cell.value or "").strip()
        fio = format_display_name(first, middle)
        if not fio:
            fio = fio_compact
        if not fio:
            fio = str(telegram_id)
        closed_raw = (closed_cell.value or "").strip()
        try:
            closed = int(closed_raw)
        except ValueError:
            closed = 0

        return UserProfile(
            telegram_id=telegram_id,
            fio=fio,
            closed_shifts=closed,
            fio_compact=fio_compact,
        )

    def open_shift_for_user(
        self, telegram_id: int, spreadsheet_id: Optional[str] = None
    ) -> int:
        """Готовит синхронизированную строку смены во всех рабочих листах."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        profile = self.get_user_profile(telegram_id, sid)
        ws_expenses = self._get_worksheet(SHEET_EXPENSES, sid)
        today_date = date.today()
        existing_row = self._find_today_row_for_user(
            ws_expenses, telegram_id, today_date
        )
        if existing_row is not None:
            target_row = existing_row
        else:
            target_row = self._compute_target_row_for_user(telegram_id, sid)

        today_value = today_date.isoformat()

        batch = [
            {
                "range": f"{SHEET_EXPENSES}!{EXPENSES_COL_DATE}{target_row}",
                "values": [[today_value]],
            },
            {
                "range": f"{SHEET_EXPENSES}!{EXPENSES_COL_TG}{target_row}",
                "values": [[str(telegram_id)]],
            },
            {
                "range": f"{SHEET_MATERIALS}!{MATERIALS_COL_TG}{target_row}",
                "values": [[str(telegram_id)]],
            },
            {
                "range": f"{SHEET_CREW}!{CREW_COL_TG}{target_row}",
                "values": [[str(telegram_id)]],
            },
            {
                "range": f"{SHEET_CREW}!{CREW_COL_FIO}{target_row}",
                "values": [[profile.fio_compact or profile.fio]],
            },
        ]

        spreadsheet = self._get_spreadsheet(sid)
        retry(
            lambda: spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": batch}
            )
        )
        return target_row

    def get_shift_row_index_for_user(
        self, telegram_id: int, spreadsheet_id: Optional[str] = None
    ) -> Optional[int]:
        """Возвращает индекс последней рабочей строки пользователя, если он есть."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_expenses = self._get_worksheet(SHEET_EXPENSES, sid)
        ws_materials = self._get_worksheet(SHEET_MATERIALS, sid)
        ws_crew = self._get_worksheet(SHEET_CREW, sid)

        last_rows = [
            self._last_row_with_tg(ws_expenses, EXPENSES_COL_TG, telegram_id),
            self._last_row_with_tg(ws_materials, MATERIALS_COL_TG, telegram_id),
            self._last_row_with_tg(ws_crew, CREW_COL_TG, telegram_id),
        ]
        candidates = [row for row in last_rows if row]
        return max(candidates) if candidates else None

    def _find_today_row_for_user(
        self,
        ws_expenses: gspread.Worksheet,
        telegram_id: int,
        today: date,
    ) -> Optional[int]:
        """Ищет строку текущей смены в листе «Расходы смены» по дате и Telegram ID."""

        date_values = retry(
            lambda: ws_expenses.col_values(self._col_to_index(EXPENSES_COL_DATE))
        )
        tg_values = retry(
            lambda: ws_expenses.col_values(self._col_to_index(EXPENSES_COL_TG))
        )
        max_length = max(len(date_values), len(tg_values))
        target_tid = _norm_tid(telegram_id)

        for offset in range(1, max_length):
            raw_tid = tg_values[offset] if offset < len(tg_values) else ""
            if _norm_tid(raw_tid) != target_tid:
                continue

            raw_date = date_values[offset] if offset < len(date_values) else ""
            cell_date = self._parse_date_value(raw_date)
            if cell_date == today:
                return offset + 1

        return None

    def get_shift_progress(
        self,
        telegram_id: int,
        row: int,
        spreadsheet_id: Optional[str] = None,
    ) -> dict[str, bool]:
        """Возвращает готовность разделов смены."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_expenses = self._get_worksheet(SHEET_EXPENSES, sid)
        ws_materials = self._get_worksheet(SHEET_MATERIALS, sid)
        ws_crew = self._get_worksheet(SHEET_CREW, sid)

        expenses_cols = getattr(self, "EXPENSES_USER_COLS", EXPENSES_USER_COLS)
        materials_cols = getattr(self, "MATERIALS_USER_COLS", MATERIALS_USER_COLS)
        crew_cols = getattr(self, "CREW_USER_COLS", CREW_USER_COLS)

        return {
            "expenses": self._row_all_filled(ws_expenses, row, expenses_cols),
            "materials": self._row_all_filled(ws_materials, row, materials_cols),
            "crew": self._row_all_filled(ws_crew, row, crew_cols),
        }

    def _compute_target_row_for_user(
        self, telegram_id: int, spreadsheet_id: Optional[str] = None
    ) -> int:
        """Определяет целевую строку оформления смены."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_expenses = self._get_worksheet(SHEET_EXPENSES, sid)
        ws_materials = self._get_worksheet(SHEET_MATERIALS, sid)
        ws_crew = self._get_worksheet(SHEET_CREW, sid)

        last_by_user = [
            self._last_row_with_tg(ws_expenses, EXPENSES_COL_TG, telegram_id),
            self._last_row_with_tg(ws_materials, MATERIALS_COL_TG, telegram_id),
            self._last_row_with_tg(ws_crew, CREW_COL_TG, telegram_id),
        ]
        filtered = [row for row in last_by_user if row]
        if filtered:
            return max(filtered) + 1

        last_global = [
            self._last_nonempty_row(ws_expenses),
            self._last_nonempty_row(ws_materials),
            self._last_nonempty_row(ws_crew),
        ]
        return max(last_global) + 1

    def _find_user_row_in_data(
        self, worksheet: gspread.Worksheet, telegram_id: int
    ) -> Optional[int]:
        """Ищет строку пользователя в листе «Данные» по Telegram ID."""

        column = retry(
            lambda: worksheet.col_values(self._col_to_index(DATA_COL_TG))
        )
        for index, value in enumerate(column[1:], start=2):
            if str(value).strip() == str(telegram_id):
                return index
        return None

    @staticmethod
    def _col_to_index(letter: str) -> int:
        """Преобразует обозначение колонки (A, B, ...) в номер."""

        return ord(letter.upper()) - ord("A") + 1

    def _last_nonempty_row(self, worksheet: gspread.Worksheet) -> int:
        """Возвращает индекс последней непустой строки по колонке A."""

        column = retry(lambda: worksheet.col_values(1))
        for index in range(len(column), 0, -1):
            if str(column[index - 1]).strip():
                return index
        return 1

    def _last_row_with_tg(
        self, worksheet: gspread.Worksheet, column_letter: str, telegram_id: int
    ) -> Optional[int]:
        """Возвращает последнюю строку пользователя по колонке с Telegram ID."""

        column = retry(
            lambda: worksheet.col_values(self._col_to_index(column_letter))
        )
        last: Optional[int] = None
        for index, value in enumerate(column[1:], start=2):
            if str(value).strip() == str(telegram_id):
                last = index
        return last

    def _row_all_filled(
        self, worksheet: gspread.Worksheet, row: int, columns: list[str]
    ) -> bool:
        """Проверяет заполнение всех указанных ячеек в строке."""

        ranges = [f"{col}{row}:{col}{row}" for col in columns]
        data = retry(
            lambda: worksheet.batch_get(ranges, value_render_option="UNFORMATTED_VALUE")
        )

        if len(data) < len(columns):
            data = list(data) + [[] for _ in range(len(columns) - len(data))]

        def _non_empty(block: list[list[Any]] | list[Any]) -> bool:
            if not block:
                return False
            first_row = block[0] if isinstance(block[0], list) else block
            if not first_row:
                return False
            value = first_row[0]
            return str(value).strip() != ""

        return all(_non_empty(cell) for cell in data)

    @staticmethod
    def _parse_date_value(value: Any) -> Optional[date]:
        """Пытается преобразовать значение ячейки в дату."""

        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()

        text = str(value).strip()
        if not text:
            return None

        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue

        return None
