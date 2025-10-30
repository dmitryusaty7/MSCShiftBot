"""Утилиты для упрощённой работы с Google Sheets в обработчиках бота."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from services.sheets import SheetsService


@dataclass(frozen=True)
class CrewWorker:
    """Элемент справочника рабочих с числовым идентификатором."""

    worker_id: int
    name: str


class CrewSheetsService:
    """Адаптер ``SheetsService`` с удобными методами для раздела «Бригада»."""

    def __init__(self, base: SheetsService | None = None) -> None:
        self._base = base or SheetsService()
        self._locally_closed_rows: set[int] = set()

    # Базовые прокси -----------------------------------------------------
    def get_shift_row_index_for_user(self, telegram_id: int) -> int | None:
        return self._base.get_shift_row_index_for_user(telegram_id)

    def open_shift_for_user(self, telegram_id: int) -> int:
        return self._base.open_shift_for_user(telegram_id)

    def get_shift_summary(self, row: int) -> dict[str, object]:
        return self._base.get_shift_summary(row)

    # Работа со справочником рабочих ------------------------------------
    def list_active_drivers(self) -> list[CrewWorker]:
        """Возвращает доступных водителей с числовыми идентификаторами."""

        names = self._base.list_active_drivers()
        return [CrewWorker(worker_id=index + 1, name=name) for index, name in enumerate(names)]

    def list_active_workers(self) -> list[CrewWorker]:
        names = self._base.list_active_workers()
        return [CrewWorker(worker_id=index + 1, name=name) for index, name in enumerate(names)]

    # Добавление сотрудников -------------------------------------------------
    def add_driver(self, name: str) -> None:
        self._base.add_driver(name)

    def add_worker(self, name: str) -> None:
        self._base.add_worker(name)

    def get_driver_status(self, name: str) -> str | None:
        return self._base.get_driver_status(name)

    def get_worker_status(self, name: str) -> str | None:
        return self._base.get_worker_status(name)

    # Сохранение состава -------------------------------------------------
    def save_crew(
        self,
        row: int,
        *,
        driver: str,
        workers: Sequence[str],
        telegram_id: int | None = None,
    ) -> None:
        """Записывает выбранный состав, не трогая остальные столбцы смены."""

        self._base.save_crew(
            row,
            driver=driver,
            workers=list(workers),
            telegram_id=telegram_id,
        )

    def base_service(self) -> SheetsService:
        """Возвращает исходный ``SheetsService`` для совместимых вызовов."""

        return self._base


class ShiftCloseSheetsService:
    """Адаптер ``SheetsService`` с операциями для закрытия смены."""

    def __init__(self, base: SheetsService | None = None) -> None:
        self._base = base or SheetsService()
        self._locally_closed_rows: set[int] = set()

    def get_shift_progress(self, user_id: int, row: int) -> dict[str, bool]:
        """Возвращает статусы готовности разделов смены."""

        return self._base.get_shift_progress(user_id, row)

    def get_shift_summary(self, row: int) -> dict[str, Any]:
        """Читает словарь с итоговыми данными смены."""

        summary = self._base.get_shift_summary(row)
        if not isinstance(summary, dict):
            raise RuntimeError("ожидались данные сводки смены в виде словаря")
        return summary

    def get_shift_date(self, row: int) -> str:
        """Возвращает дату смены в исходном формате из таблицы."""

        return self._base.get_shift_date(row)

    def get_user_profile(self, user_id: int):
        """Прокси к методу ``SheetsService.get_user_profile``."""

        return self._base.get_user_profile(user_id)

    def is_shift_closed(self, row: int) -> bool:
        """Проверяет факт закрытия смены в таблице."""

        return self._base.is_shift_closed(row)

    def mark_shift_closed(
        self,
        row: int,
        user_id: int,
        timestamp: datetime | None = None,
    ) -> bool:
        """Отмечает закрытие смены локально, не изменяя таблицы."""

        if row in self._locally_closed_rows:
            return False

        try:
            already_closed = self._base.is_shift_closed(row)
        except Exception:  # pragma: no cover - сетевые ошибки обрабатываются выше
            already_closed = False

        self._locally_closed_rows.add(row)
        return not already_closed

    def base_service(self) -> SheetsService:
        """Возвращает исходный ``SheetsService``."""

        return self._base
