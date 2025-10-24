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
import socket
import time
from dataclasses import dataclass
from datetime import date, datetime
from itertools import zip_longest
from typing import Any, Callable, Optional, Tuple, TypeVar
from threading import RLock

import gspread
from gspread.exceptions import APIError
from google.auth.exceptions import TransportError
from google.oauth2.service_account import Credentials
from requests.exceptions import SSLError as RequestsSSLError
from urllib3.exceptions import SSLError as Urllib3SSLError

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

EXP_COL_SHIP = "C"
EXP_COL_HOLDS = "D"
EXP_COL_E = "E"
EXP_COL_F = "F"
EXP_COL_G = "G"
EXP_COL_H = "H"
EXP_COL_I = "I"
EXP_COL_J = "J"
EXP_COL_K = "K"
EXP_COL_TOTAL: str | None = None
EXP_COL_CLOSED_AT = "L"

MATERIALS_COL_TG = "B"
MATERIALS_COL_FIO = "E"

MAT_COL_PVD_M = "H"
MAT_COL_PVC_PCS = "I"
MAT_COL_TAPE_PCS = "J"
MAT_COL_FOLDER_LINK = "N"

CREW_COL_TG = "B"
CREW_COL_DRIVER = "E"
CREW_COL_BRIGADIER = "F"
CREW_COL_WORKERS = "G"

DATA_COL_TG = "A"
DATA_COL_FIO = "E"
DATA_COL_CLOSED_SHIFTS = "F"
DATA_COL_SHIP = "H"
DATA_COL_STATUS = "I"

# пользовательские столбцы, которые нужно заполнить вручную
EXPENSES_USER_COLS = [chr(code) for code in range(ord("C"), ord("K") + 1)]
MATERIALS_USER_COLS = ["A"] + [
    column
    for column in map(chr, range(ord("C"), ord("N") + 1))
    if column != MATERIALS_COL_FIO
]
CREW_USER_COLS = [CREW_COL_TG, CREW_COL_DRIVER, CREW_COL_BRIGADIER, CREW_COL_WORKERS]

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
        except (
            RequestsSSLError,
            Urllib3SSLError,
            socket.timeout,
            TransportError,
        ) as error:
            if attempt >= tries:
                logger.error("Ошибка соединения после %s попыток: %s", tries, error)
                raise

            logger.warning(
                "Повтор %s/%s после сетевой ошибки: %s", attempt, tries, error
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
    MATERIALS_COL_FIO = MATERIALS_COL_FIO
    CREW_USER_COLS = CREW_USER_COLS
    EXP_COL_SHIP = EXP_COL_SHIP
    EXP_COL_HOLDS = EXP_COL_HOLDS
    EXP_COL_E = EXP_COL_E
    EXP_COL_F = EXP_COL_F
    EXP_COL_G = EXP_COL_G
    EXP_COL_H = EXP_COL_H
    EXP_COL_I = EXP_COL_I
    EXP_COL_J = EXP_COL_J
    EXP_COL_K = EXP_COL_K
    EXP_COL_TOTAL = EXP_COL_TOTAL
    DATA_COL_SHIP = DATA_COL_SHIP
    DATA_COL_STATUS = DATA_COL_STATUS

    def __init__(self) -> None:
        self.client = get_client()
        self._spreadsheet_cache: dict[str, gspread.Spreadsheet] = {}
        self._worksheet_cache: dict[tuple[str, str], gspread.Worksheet] = {}
        self._closed_rows: set[tuple[str, int]] = set()
        self._closed_rows_lock = RLock()

    @property
    def spreadsheet(self) -> gspread.Spreadsheet:
        """Возвращает таблицу из настроек по умолчанию."""

        return self._get_spreadsheet()

    @property
    def ws_materials(self) -> gspread.Worksheet:
        """Возвращает лист «Материалы» из таблицы по умолчанию."""

        return self._get_worksheet(SHEET_MATERIALS)

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

    def get_active_ships(
        self, spreadsheet_id: Optional[str] = None
    ) -> list[str]:
        """Возвращает список активных судов из листа «Данные»."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_data = self._get_worksheet(SHEET_DATA, sid)
        ship_col = getattr(self, "DATA_COL_SHIP", DATA_COL_SHIP)
        status_col = getattr(self, "DATA_COL_STATUS", DATA_COL_STATUS)

        ship_values = retry(
            lambda: ws_data.col_values(self._col_to_index(ship_col))
        )
        status_values = retry(
            lambda: ws_data.col_values(self._col_to_index(status_col))
        )

        result: list[str] = []
        for name, status in zip_longest(
            ship_values[1:], status_values[1:], fillvalue=""
        ):
            clean_name = (name or "").strip()
            if not clean_name:
                continue
            clean_status = (status or "").strip().lower()
            if clean_status == "архив":
                continue
            result.append(clean_name)
        return result

    def add_ship(
        self, ship_name: str, spreadsheet_id: Optional[str] = None
    ) -> None:
        """Добавляет новое судно в конец списка и помечает как активное."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_data = self._get_worksheet(SHEET_DATA, sid)
        ship_col = getattr(self, "DATA_COL_SHIP", DATA_COL_SHIP)
        status_col = getattr(self, "DATA_COL_STATUS", DATA_COL_STATUS)

        last_row = self._last_nonempty_row_in_column(ws_data, ship_col)
        next_row = max(last_row + 1, 2)

        ship_clean = ship_name.strip()
        spreadsheet = self._get_spreadsheet(sid)
        updates = [
            {
                "range": f"{ws_data.title}!{ship_col}{next_row}",
                "values": [[ship_clean]],
            },
            {
                "range": f"{ws_data.title}!{status_col}{next_row}",
                "values": [["Активен"]],
            },
        ]
        retry(
            lambda: spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": updates}
            )
        )

    def open_shift_for_user(
        self, telegram_id: int, spreadsheet_id: Optional[str] = None
    ) -> int:
        """Готовит синхронизированную строку смены во всех рабочих листах."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_expenses = self._get_worksheet(SHEET_EXPENSES, sid)
        today_date = date.today()
        existing_row = self._find_today_row_for_user(
            ws_expenses, telegram_id, today_date
        )
        if existing_row is not None:
            target_row = existing_row
        else:
            target_row = self._compute_target_row_for_user(telegram_id, sid)

        profile = self.get_user_profile(telegram_id, sid)

        today_value = today_date.isoformat()
        fio_value = (profile.fio or "").strip()
        materials_fio_col = getattr(self, "MATERIALS_COL_FIO", MATERIALS_COL_FIO)

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
                "range": f"{SHEET_MATERIALS}!{materials_fio_col}{target_row}",
                "values": [[fio_value]],
            },
            {
                "range": f"{SHEET_CREW}!{CREW_COL_TG}{target_row}",
                "values": [[str(telegram_id)]],
            },
        ]

        if existing_row is None:
            batch.extend(
                [
                    {
                        "range": f"{SHEET_CREW}!{CREW_COL_DRIVER}{target_row}",
                        "values": [[""]],
                    },
                    {
                        "range": f"{SHEET_CREW}!{CREW_COL_BRIGADIER}{target_row}",
                        "values": [[""]],
                    },
                    {
                        "range": f"{SHEET_CREW}!{CREW_COL_WORKERS}{target_row}",
                        "values": [[""]],
                    },
                ]
            )

        spreadsheet = self._get_spreadsheet(sid)
        retry(
            lambda: spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": batch}
            )
        )
        try:
            self._ensure_brigadier_autofill(
                telegram_id,
                target_row,
                sid,
                profile=profile,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Не удалось автоматически заполнить бригадира (user_id=%s, row=%s)",
                telegram_id,
                target_row,
            )
        return target_row

    def _ensure_brigadier_autofill(
        self,
        telegram_id: int,
        row: int,
        spreadsheet_id: Optional[str],
        *,
        profile: UserProfile | None = None,
    ) -> None:
        """Записывает ФИО бригадира в лист «Состав бригады», если ячейка пуста."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_crew = self._get_worksheet(SHEET_CREW, sid)
        cell = retry(lambda: ws_crew.acell(f"{CREW_COL_BRIGADIER}{row}"))
        current_value = (cell.value or "").strip()
        if current_value:
            return

        if profile is None:
            profile = self.get_user_profile(telegram_id, sid)
        candidate = (profile.fio_compact or profile.fio or "").strip()
        if not candidate:
            return

        spreadsheet = self._get_spreadsheet(sid)
        retry(
            lambda: spreadsheet.values_batch_update(
                {
                    "valueInputOption": "USER_ENTERED",
                    "data": [
                        {
                            "range": f"{ws_crew.title}!{CREW_COL_BRIGADIER}{row}",
                            "values": [[candidate]],
                        }
                    ],
                }
            )
        )

    def save_expenses_block(
        self,
        telegram_id: int,
        row: int,
        spreadsheet_id: Optional[str] = None,
        *,
        ship: str | None = None,
        holds: int | None = None,
        e: int | None = None,
        f: int | None = None,
        g: int | None = None,
        h: int | None = None,
        i: int | None = None,
        j: int | None = None,
        k: int | None = None,
    ) -> None:
        """Сохраняет указанные поля в листе «Расходы смены» для строки смены."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_expenses = self._get_worksheet(SHEET_EXPENSES, sid)
        logger.info(
            "Сохранение блока расходов: user_id=%s, row=%s", telegram_id, row
        )

        ship_col = getattr(self, "EXP_COL_SHIP", EXP_COL_SHIP)
        holds_col = getattr(self, "EXP_COL_HOLDS", EXP_COL_HOLDS)
        e_col = getattr(self, "EXP_COL_E", EXP_COL_E)
        f_col = getattr(self, "EXP_COL_F", EXP_COL_F)
        g_col = getattr(self, "EXP_COL_G", EXP_COL_G)
        h_col = getattr(self, "EXP_COL_H", EXP_COL_H)
        i_col = getattr(self, "EXP_COL_I", EXP_COL_I)
        j_col = getattr(self, "EXP_COL_J", EXP_COL_J)
        k_col = getattr(self, "EXP_COL_K", EXP_COL_K)

        pending: list[tuple[str, list[list[Any]]]] = []

        def put(column: str, value: Any) -> None:
            if value is not None:
                pending.append((f"{ws_expenses.title}!{column}{row}", [[value]]))

        put(ship_col, ship)
        put(holds_col, holds)
        put(e_col, e)
        put(f_col, f)
        put(g_col, g)
        put(h_col, h)
        put(i_col, i)
        put(j_col, j)
        put(k_col, k)

        if not pending:
            return

        spreadsheet = self._get_spreadsheet(sid)
        retry(
            lambda: spreadsheet.values_batch_update(
                {
                    "valueInputOption": "USER_ENTERED",
                    "data": [
                        {"range": cell_range, "values": values}
                        for cell_range, values in pending
                    ],
                }
            )
        )

    def save_materials_block(
        self,
        row: int,
        spreadsheet_id: Optional[str] = None,
        *,
        pvd_m: int | None = None,
        pvc_pcs: int | None = None,
        tape_pcs: int | None = None,
        folder_link: str | None = None,
    ) -> None:
        """Сохраняет блок материалов для строки смены."""

        ws = self._get_worksheet(SHEET_MATERIALS, spreadsheet_id)
        updates: list[dict[str, object]] = []

        def put(column: str, value: Any) -> None:
            if value is not None:
                updates.append(
                    {
                        "range": f"{ws.title}!{column}{row}",
                        "values": [[value]],
                    }
                )

        put(MAT_COL_PVD_M, pvd_m)
        put(MAT_COL_PVC_PCS, pvc_pcs)
        put(MAT_COL_TAPE_PCS, tape_pcs)
        put(MAT_COL_FOLDER_LINK, folder_link)

        if not updates:
            return

        spreadsheet = self._get_spreadsheet(spreadsheet_id)
        retry(
            lambda: spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": updates}
            )
        )

    def _list_directory_records(
        self,
        name_column: str,
        status_column: str,
        spreadsheet_id: Optional[str] = None,
    ) -> list[tuple[str, str]]:
        """Возвращает пары (имя, статус) из колонок справочника на листе «Данные».

        Пустые строки игнорируются, значения обрезаются по краям.
        """

        ws_data = self._get_worksheet(SHEET_DATA, spreadsheet_id)
        range_label = f"{name_column}2:{status_column}"
        values = retry(lambda: ws_data.get(range_label))

        records: list[tuple[str, str]] = []
        for row in values:
            if not row:
                continue
            name = str(row[0]).strip() if len(row) >= 1 else ""
            status = str(row[1]).strip() if len(row) >= 2 else ""
            if name:
                records.append((name, status))
        return records

    def list_active_drivers(
        self, spreadsheet_id: Optional[str] = None
    ) -> list[str]:
        """Возвращает список активных водителей в порядке следования в таблице."""

        records = self._list_directory_records("J", "K", spreadsheet_id)
        result: list[str] = []
        seen: set[str] = set()
        for name, status in records:
            if status.strip().lower() == "архив":
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(name)
        return result

    def list_active_workers(
        self, spreadsheet_id: Optional[str] = None
    ) -> list[str]:
        """Возвращает список активных рабочих."""

        records = self._list_directory_records("L", "M", spreadsheet_id)
        result: list[str] = []
        seen: set[str] = set()
        for name, status in records:
            if status.strip().lower() == "архив":
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(name)
        return result

    def _directory_status(
        self,
        name: str,
        name_column: str,
        status_column: str,
        spreadsheet_id: Optional[str] = None,
    ) -> Optional[str]:
        """Возвращает статус записи по имени или ``None``, если запись не найдена."""

        name_key = name.casefold()
        for existing, status in self._list_directory_records(
            name_column, status_column, spreadsheet_id
        ):
            if existing.casefold() == name_key:
                return status or "Активен"
        return None

    def get_driver_status(
        self, name: str, spreadsheet_id: Optional[str] = None
    ) -> Optional[str]:
        """Возвращает статус водителя или ``None``, если его нет в таблице."""

        return self._directory_status(name, "J", "K", spreadsheet_id)

    def get_worker_status(
        self, name: str, spreadsheet_id: Optional[str] = None
    ) -> Optional[str]:
        """Возвращает статус рабочего или ``None``, если запись не найдена."""

        return self._directory_status(name, "L", "M", spreadsheet_id)

    def add_driver(
        self, name: str, spreadsheet_id: Optional[str] = None
    ) -> None:
        """Добавляет нового водителя со статусом «Активен» в лист «Данные».

        Запись добавляется в первый свободный ряд соответствующих колонок.
        """

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_data = self._get_worksheet(SHEET_DATA, sid)
        next_row = max(self._last_nonempty_row_in_column(ws_data, "J") + 1, 2)
        spreadsheet = self._get_spreadsheet(sid)
        updates = [
            {
                "range": f"{ws_data.title}!J{next_row}",
                "values": [[name.strip()]],
            },
            {
                "range": f"{ws_data.title}!K{next_row}",
                "values": [["Активен"]],
            },
        ]
        retry(
            lambda: spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": updates}
            )
        )

    def add_worker(
        self, name: str, spreadsheet_id: Optional[str] = None
    ) -> None:
        """Добавляет нового рабочего со статусом «Активен» в лист «Данные»."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_data = self._get_worksheet(SHEET_DATA, sid)
        next_row = max(self._last_nonempty_row_in_column(ws_data, "L") + 1, 2)
        spreadsheet = self._get_spreadsheet(sid)
        updates = [
            {
                "range": f"{ws_data.title}!L{next_row}",
                "values": [[name.strip()]],
            },
            {
                "range": f"{ws_data.title}!M{next_row}",
                "values": [["Активен"]],
            },
        ]
        retry(
            lambda: spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": updates}
            )
        )

    def save_crew(
        self,
        row: int,
        *,
        driver: str,
        workers: list[str],
        telegram_id: int | None = None,
        spreadsheet_id: Optional[str] = None,
    ) -> None:
        """Сохраняет выбранного водителя и список рабочих для строки смены."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_crew = self._get_worksheet(SHEET_CREW, sid)
        spreadsheet = self._get_spreadsheet(sid)

        workers_clean = [worker.strip() for worker in workers if worker.strip()]
        workers_value = ", ".join(workers_clean)

        updates: list[dict[str, object]] = [
            {
                "range": f"{ws_crew.title}!{CREW_COL_DRIVER}{row}",
                "values": [[driver.strip()]],
            },
            {
                "range": f"{ws_crew.title}!{CREW_COL_WORKERS}{row}",
                "values": [[workers_value]],
            },
        ]

        if telegram_id is not None:
            updates.append(
                {
                    "range": f"{ws_crew.title}!{CREW_COL_TG}{row}",
                    "values": [[str(telegram_id)]],
                }
            )

        retry(
            lambda: spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": updates}
            )
        )

    def get_shift_summary(
        self, row: int, spreadsheet_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Собирает данные смены из листов расходов, материалов и бригады."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        ws_expenses = self._get_worksheet(SHEET_EXPENSES, sid)
        ws_materials = self._get_worksheet(SHEET_MATERIALS, sid)
        ws_crew = self._get_worksheet(SHEET_CREW, sid)

        def fetch_row(
            worksheet: gspread.Worksheet,
            columns: list[str],
            *,
            value_option: str = "UNFORMATTED_VALUE",
        ) -> list[Any]:
            ranges = [f"{column}{row}:{column}{row}" for column in columns]
            data = retry(
                lambda: worksheet.batch_get(
                    ranges, value_render_option=value_option
                )
            )
            values: list[Any] = []
            for block in data:
                if not block:
                    values.append("")
                    continue
                first = block[0]
                if isinstance(first, list):
                    values.append(first[0] if first else "")
                else:
                    values.append(first)
            while len(values) < len(columns):
                values.append("")
            return values

        def parse_int(value: Any) -> int:
            if value is None:
                return 0
            if isinstance(value, (int, float)):
                try:
                    return int(value)
                except (ValueError, TypeError):  # pragma: no cover - защитное ветвление
                    return 0
            text = str(value).strip().replace("\u202f", "").replace(" ", "")
            if not text:
                return 0
            try:
                if "." in text:
                    return int(float(text))
                return int(text)
            except ValueError:
                digits = "".join(ch for ch in text if ch.isdigit() or ch == "-")
                return int(digits) if digits else 0

        total_column = getattr(self, "EXP_COL_TOTAL", EXP_COL_TOTAL)
        expenses_columns = [
            EXPENSES_COL_DATE,
            EXP_COL_SHIP,
            EXP_COL_HOLDS,
            EXP_COL_E,
            EXP_COL_F,
            EXP_COL_G,
            EXP_COL_H,
            EXP_COL_I,
            EXP_COL_J,
            EXP_COL_K,
        ]
        include_total = bool(total_column)
        if include_total and total_column not in expenses_columns:
            expenses_columns.append(total_column)
        expenses_values = fetch_row(ws_expenses, expenses_columns)

        date_value = expenses_values[0]
        if isinstance(date_value, datetime):
            date_text = date_value.date().isoformat()
        elif isinstance(date_value, date):
            date_text = date_value.isoformat()
        else:
            date_text = str(date_value).strip()
        if not date_text:
            date_text = date.today().isoformat()

        ship_value = str(expenses_values[1]).strip()
        holds_value = parse_int(expenses_values[2])
        driver_cost = parse_int(expenses_values[3])
        brigadier_cost = parse_int(expenses_values[4])
        workers_cost = parse_int(expenses_values[5])
        aux_cost = parse_int(expenses_values[6])
        food_cost = parse_int(expenses_values[7])
        taxi_cost = parse_int(expenses_values[8])
        other_cost = parse_int(expenses_values[9])
        total_raw = (
            expenses_values[10]
            if include_total and len(expenses_values) > 10
            else None
        )
        total_cost = parse_int(total_raw) if total_raw is not None else 0
        if total_cost == 0:
            total_candidate = (
                driver_cost
                + brigadier_cost
                + workers_cost
                + aux_cost
                + food_cost
                + taxi_cost
                + other_cost
            )
            if total_candidate:
                total_cost = total_candidate

        materials_columns = [
            MAT_COL_PVD_M,
            MAT_COL_PVC_PCS,
            MAT_COL_TAPE_PCS,
            MAT_COL_FOLDER_LINK,
        ]
        materials_values = fetch_row(ws_materials, materials_columns)
        pvd_value = parse_int(materials_values[0])
        pvc_value = parse_int(materials_values[1])
        tape_value = parse_int(materials_values[2])
        photos_link = str(materials_values[3]).strip()

        crew_columns = [CREW_COL_DRIVER, CREW_COL_WORKERS]
        crew_values = fetch_row(
            ws_crew, crew_columns, value_option="FORMATTED_VALUE"
        )
        driver_name = str(crew_values[0]).strip()
        workers_line = str(crew_values[1]).strip()
        if workers_line:
            workers_list = [
                piece.strip()
                for piece in re.split(r"[;,]", workers_line)
                if piece.strip()
            ]
        else:
            workers_list = []

        return {
            "date": date_text,
            "ship": ship_value,
            "holds": holds_value,
            "expenses": {
                "driver": driver_cost,
                "brigadier": brigadier_cost,
                "workers": workers_cost,
                "aux": aux_cost,
                "food": food_cost,
                "taxi": taxi_cost,
                "other": other_cost,
                "total": total_cost,
            },
            "materials": {
                "pvd_rolls_m": pvd_value,
                "pvc_tubes": pvc_value,
                "tape": tape_value,
                "photos_link": photos_link or None,
            },
            "crew": {
                "driver": driver_name,
                "workers": workers_list,
            },
        }

    def materials_all_filled(
        self, row: int, spreadsheet_id: Optional[str] = None
    ) -> bool:
        """Проверяет заполнение всех обязательных полей материалов."""

        ws = self._get_worksheet(SHEET_MATERIALS, spreadsheet_id)
        ranges = [
            f"{col}{row}:{col}{row}"
            for col in (MAT_COL_PVD_M, MAT_COL_PVC_PCS, MAT_COL_TAPE_PCS, MAT_COL_FOLDER_LINK)
        ]
        cells = retry(
            lambda: ws.batch_get(ranges, value_render_option="UNFORMATTED_VALUE")
        )

        def extract(block: list[list[Any]] | list[Any]) -> str:
            if not block:
                return ""
            first_row = block[0] if isinstance(block[0], list) else block
            if not first_row:
                return ""
            return str(first_row[0]).strip()

        values = [extract(block) for block in cells]
        while len(values) < 4:
            values.append("")

        h_val, i_val, j_val, n_val = values

        def is_num(value: str) -> bool:
            return bool(re.fullmatch(r"\d+", value))

        return is_num(h_val) and is_num(i_val) and is_num(j_val) and bool(n_val)

    def crew_all_filled(
        self, row: int, spreadsheet_id: Optional[str] = None
    ) -> bool:
        """Возвращает ``True``, если в строке выбраны водитель и хотя бы один рабочий."""

        ws = self._get_worksheet(SHEET_CREW, spreadsheet_id)
        ranges = [
            f"{CREW_COL_DRIVER}{row}:{CREW_COL_DRIVER}{row}",
            f"{CREW_COL_WORKERS}{row}:{CREW_COL_WORKERS}{row}",
        ]
        cells = retry(
            lambda: ws.batch_get(ranges, value_render_option="UNFORMATTED_VALUE")
        )

        def extract(block: list[list[Any]] | list[Any]) -> str:
            if not block:
                return ""
            first_row = block[0] if isinstance(block[0], list) else block
            if not first_row:
                return ""
            return str(first_row[0]).strip()

        values = [extract(block) for block in cells]
        while len(values) < 2:
            values.append("")

        driver_value, workers_value = values
        return bool(driver_value) and bool(workers_value)

    def is_shift_closed(
        self, row: int, spreadsheet_id: Optional[str] = None
    ) -> bool:
        """Проверяет, закрыта ли смена (по кешу и исторической отметке в листе расходов)."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        cache_key = (sid, row)
        with self._closed_rows_lock:
            if cache_key in self._closed_rows:
                return True

        closed_column = getattr(self, "EXP_COL_CLOSED_AT", EXP_COL_CLOSED_AT)
        if not closed_column:
            return False

        ws_expenses = self._get_worksheet(SHEET_EXPENSES, sid)
        cell = retry(lambda: ws_expenses.acell(f"{closed_column}{row}"))
        is_closed = bool((cell.value or "").strip())
        if is_closed:
            with self._closed_rows_lock:
                self._closed_rows.add(cache_key)
        return is_closed

    def finalize_shift(
        self,
        user_id: int,
        row: int,
        spreadsheet_id: Optional[str] = None,
    ) -> bool:
        """Помечает смену закрытой в рамках текущего запуска без записи в таблицу."""

        sid = spreadsheet_id or require_env("SPREADSHEET_ID")
        cache_key = (sid, row)
        with self._closed_rows_lock:
            if cache_key in self._closed_rows:
                logger.info("Смена уже закрыта (user_id=%s, row=%s)", user_id, row)
                return False
            self._closed_rows.add(cache_key)
        logger.info("Смена закрыта (user_id=%s, row=%s)", user_id, row)
        return True

    def _last_nonempty_row_in_column(
        self, worksheet: gspread.Worksheet, column_letter: str
    ) -> int:
        """Возвращает номер последней непустой строки в указанном столбце."""

        values = retry(
            lambda: worksheet.col_values(self._col_to_index(column_letter))
        )
        for index in range(len(values), 0, -1):
            if str(values[index - 1]).strip():
                return index
        return 1

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

        expenses_cols = getattr(self, "EXPENSES_USER_COLS", EXPENSES_USER_COLS)

        return {
            "expenses": self._row_all_filled(ws_expenses, row, expenses_cols),
            "materials": self.materials_all_filled(row, sid),
            "crew": self.crew_all_filled(row, sid),
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
