"""Модуль для работы с Google Sheets в рамках смен MSC.

Все обращения вынесены в отдельный сервис, чтобы упростить тестирование и
перевести синхронный клиент gspread в асинхронный сценарий через to_thread.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import base64
import os

import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


@dataclass
class ShiftRowInfo:
    """Информация о строках, созданных для активной смены."""

    expenses_row: int
    materials_row: int
    crew_row: int


def _load_service_account_from_path(path: str) -> Dict[str, Any]:
    """Читает файл service_account.json и возвращает словарь с данными."""

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_service_account_from_env(
    json_env_var: str = "GOOGLE_SERVICE_ACCOUNT_JSON",
    base64_env_var: str = "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64",
) -> Dict[str, Any]:
    """Получает данные сервисного аккаунта из переменных окружения."""

    raw_json = os.getenv(json_env_var)
    if raw_json:
        return json.loads(raw_json)

    raw_base64 = os.getenv(base64_env_var)
    if raw_base64:
        decoded = base64.b64decode(raw_base64)
        return json.loads(decoded.decode("utf-8"))

    raise RuntimeError(
        "Не удалось найти данные сервисного аккаунта: "
        f"установите {json_env_var} или {base64_env_var}"
    )


def load_service_account_info(
    service_account_path: Optional[str] = None,
    json_env_var: str = "GOOGLE_SERVICE_ACCOUNT_JSON",
    base64_env_var: str = "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64",
) -> Dict[str, Any]:
    """Загружает настройки сервисного аккаунта из файла или окружения."""

    if service_account_path:
        return _load_service_account_from_path(service_account_path)

    return _load_service_account_from_env(json_env_var, base64_env_var)


def build_credentials(service_account_info: Dict[str, Any]) -> Credentials:
    """Создаёт объект Credentials для работы с Google API."""

    return Credentials.from_service_account_info(service_account_info, scopes=SCOPES)


class SheetsService:
    """Обертка над gspread для работы с таблицами проекта."""

    def __init__(self, spreadsheet_id: str, credentials: Credentials) -> None:
        self._spreadsheet_id = spreadsheet_id
        self._credentials = credentials

    @classmethod
    def from_service_account(
        cls,
        spreadsheet_id: str,
        service_account_path: Optional[str] = None,
        json_env_var: str = "GOOGLE_SERVICE_ACCOUNT_JSON",
        base64_env_var: str = "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64",
    ) -> "SheetsService":
        """Создаёт сервис, используя файл или переменные окружения."""

        info = load_service_account_info(
            service_account_path=service_account_path,
            json_env_var=json_env_var,
            base64_env_var=base64_env_var,
        )
        credentials = build_credentials(info)
        return cls(spreadsheet_id=spreadsheet_id, credentials=credentials)

    def _get_client(self) -> gspread.Client:
        return gspread.authorize(self._credentials)

    async def find_user_row(self, telegram_id: int) -> Optional[int]:
        """Возвращает номер строки пользователя или None."""

        def _find() -> Optional[int]:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Данные")
            ids = sheet.col_values(1)
            for index, value in enumerate(ids, start=1):
                if str(telegram_id) == value.strip():
                    return index
            return None

        return await asyncio.to_thread(_find)

    async def ensure_user_row(self, telegram_id: int) -> int:
        """Находит или создает строку пользователя и возвращает её номер."""

        existing = await self.find_user_row(telegram_id)
        if existing is not None:
            return existing

        def _create() -> int:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Данные")
            sheet.append_row([str(telegram_id)])
            ids = sheet.col_values(1)
            return len(ids)

        return await asyncio.to_thread(_create)

    async def update_user_fio(
        self,
        row: int,
        last_name: str,
        first_name: str,
        middle_name: str,
    ) -> None:
        """Обновляет ячейки с ФИО пользователя."""

        def _update() -> None:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Данные")
            safe_middle = middle_name if middle_name else "-"
            sheet.update(
                f"B{row}:D{row}",
                [[last_name.strip(), first_name.strip(), safe_middle.strip()]],
                value_input_option="USER_ENTERED",
            )

        await asyncio.to_thread(_update)

    async def get_user_fio(self, row: int) -> str:
        """Возвращает склеенное ФИО из трёх столбцов."""

        def _get() -> str:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Данные")
            values = sheet.get(f"B{row}:D{row}")
            if not values:
                return ""
            fio_parts = [part for part in values[0] if part]
            return " ".join(fio_parts)

        return await asyncio.to_thread(_get)

    async def get_dashboard_info(self, row: int) -> Dict[str, str]:
        """Возвращает данные для дашборда."""

        def _get() -> Dict[str, str]:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Данные")
            first_name = sheet.acell(f"C{row}").value or "Коллега"
            middle_name = sheet.acell(f"D{row}").value or ""
            display_name = " ".join(filter(None, [first_name, middle_name])).strip() or "Коллега"
            last_closed = sheet.acell(f"E{row}").value or ""
            closed_counter = sheet.acell(f"F{row}").value or "0"
            return {
                "display_name": display_name,
                "last_closed": last_closed,
                "closed_count": closed_counter,
            }

        return await asyncio.to_thread(_get)

    async def append_shift_rows(self, telegram_id: int, fio: str) -> ShiftRowInfo:
        """Создает записи во всех листах для новой смены."""

        def _append() -> ShiftRowInfo:
            client = self._get_client()
            spreadsheet = client.open_by_key(self._spreadsheet_id)
            now_formula = "=TODAY()"

            def _append_with_index(ws_name: str, base_row: List[str]) -> int:
                ws = spreadsheet.worksheet(ws_name)
                next_row_index = len(ws.get_all_values()) + 1
                ws.append_row(base_row, value_input_option="USER_ENTERED")
                return next_row_index

            expenses_row = _append_with_index(
                "Расходы смены",
                [
                    now_formula,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "Черновик",
                    str(telegram_id),
                    fio,
                ],
            )
            materials_row = _append_with_index(
                "Материалы",
                [
                    now_formula,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
            )
            crew_row = _append_with_index(
                "Состав бригады",
                [now_formula, "", "", fio, "", ""],
            )
            return ShiftRowInfo(
                expenses_row=expenses_row,
                materials_row=materials_row,
                crew_row=crew_row,
            )

        return await asyncio.to_thread(_append)

    async def _get_directory_by_header(self, header_token: str) -> List[str]:
        """Возвращает значения столбца по части заголовка."""

        def _read() -> List[str]:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Данные")
            headers = sheet.row_values(1)
            target_index: Optional[int] = None
            for idx, title in enumerate(headers, start=1):
                if header_token.lower() in title.lower():
                    target_index = idx
                    break
            if target_index is None:
                return []
            values = sheet.col_values(target_index)[1:]
            return [value.strip() for value in values if value and value.strip()]

        return await asyncio.to_thread(_read)

    async def get_drivers_directory(self) -> List[str]:
        """Возвращает справочник водителей."""

        return await self._get_directory_by_header("водител")

    async def get_workers_directory(self) -> List[str]:
        """Возвращает справочник рабочих."""

        return await self._get_directory_by_header("рабоч")

    async def get_ships_directory(self) -> List[str]:
        """Возвращает справочник судов."""

        return await self._get_directory_by_header("суд")

    async def get_materials_details(self, row: int) -> Dict[str, str]:
        """Возвращает сохранённые данные по материалам."""

        def _get() -> Dict[str, str]:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Материалы")
            keys = [
                "pvd_in",
                "pvc_in",
                "tape_in",
                "pvd_out",
                "pvc_out",
                "tape_out",
            ]
            data: Dict[str, str] = {key: "" for key in keys}
            values = sheet.get(f"D{row}:I{row}")
            if values:
                row_values = values[0]
                for idx, key in enumerate(keys):
                    if idx < len(row_values) and row_values[idx] is not None:
                        data[key] = row_values[idx]
            link = sheet.acell(f"M{row}").value or ""
            data["photo_link"] = link
            return data

        return await asyncio.to_thread(_get)

    async def save_materials_numbers(self, row: int, values: Dict[str, str]) -> None:
        """Сохраняет числовые показатели материалов (столбцы D–I)."""

        def _save() -> None:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Материалы")
            keys = [
                "pvd_in",
                "pvc_in",
                "tape_in",
                "pvd_out",
                "pvc_out",
                "tape_out",
            ]
            sheet.update(
                f"D{row}:I{row}",
                [[values.get(key, "") for key in keys]],
                value_input_option="USER_ENTERED",
            )

        await asyncio.to_thread(_save)

    async def save_materials_photo_link(self, row: int, link: str) -> None:
        """Сохраняет ссылку на папку с фотографиями в колонке M."""

        def _save() -> None:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Материалы")
            sheet.update(f"M{row}", link, value_input_option="USER_ENTERED")

        await asyncio.to_thread(_save)

    async def register_materials_photos(self, row: int, file_ids: List[str]) -> str:
        """Регистрирует фотографии и возвращает текст для отображения."""

        if not file_ids:
            return ""

        placeholder_link = f"Telegram файлы: {', '.join(file_ids)}"
        await self.save_materials_photo_link(row, placeholder_link)
        return placeholder_link

    async def save_expenses_details(self, row: int, values: Dict[str, str]) -> None:
        """Сохраняет детальные расходы смены (столбцы B–J)."""

        def _save() -> None:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Расходы смены")
            ordered_keys = [
                "ship",
                "holds",
                "transport",
                "foreman",
                "workers",
                "aux",
                "food",
                "taxi",
                "other",
            ]
            sheet.update(
                f"B{row}:J{row}",
                [[values.get(key, "") for key in ordered_keys]],
                value_input_option="USER_ENTERED",
            )

        await asyncio.to_thread(_save)

    async def get_expenses_details(self, row: int) -> Dict[str, str]:
        """Возвращает сохранённые данные расходов по строке."""

        def _get() -> Dict[str, str]:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Расходы смены")
            ordered_keys = [
                "ship",
                "holds",
                "transport",
                "foreman",
                "workers",
                "aux",
                "food",
                "taxi",
                "other",
            ]
            data: Dict[str, str] = {key: "" for key in ordered_keys}
            values = sheet.get(f"B{row}:J{row}")
            if values:
                row_values = values[0]
                for idx, key in enumerate(ordered_keys):
                    if idx < len(row_values) and row_values[idx] is not None:
                        data[key] = row_values[idx]
            return data

        return await asyncio.to_thread(_get)

    async def save_crew(self, row: int, driver: str, workers: List[str]) -> None:
        """Сохраняет состав бригады в листе."""

        def _save() -> None:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Состав бригады")
            sheet.update(
                f"E{row}:F{row}",
                [[driver, ", ".join(workers)]],
                value_input_option="USER_ENTERED",
            )

        await asyncio.to_thread(_save)

    async def finalize_shift(self, row: int) -> None:
        """Отмечает смену закрытой в листе расходов."""

        def _save() -> None:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Расходы смены")
            sheet.update(f"K{row}", "Закрыта", value_input_option="USER_ENTERED")

        await asyncio.to_thread(_save)

    async def update_closure_info(self, row: int) -> None:
        """Обновляет информацию о последней закрытой смене и счётчике."""

        def _update() -> None:
            client = self._get_client()
            sheet = client.open_by_key(self._spreadsheet_id).worksheet("Данные")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            last_closed_cell = f"E{row}"
            counter_cell = f"F{row}"
            current_counter = sheet.acell(counter_cell).value
            try:
                counter_value = int(current_counter) if current_counter else 0
            except ValueError:
                counter_value = 0
            sheet.update(last_closed_cell, timestamp, value_input_option="USER_ENTERED")
            sheet.update(counter_cell, str(counter_value + 1), value_input_option="USER_ENTERED")

        await asyncio.to_thread(_update)

