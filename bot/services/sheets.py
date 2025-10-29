"""Утилиты для упрощённой работы с Google Sheets в обработчиках бота."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

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

    # Базовые прокси -----------------------------------------------------
    def get_shift_row_index_for_user(self, telegram_id: int) -> int | None:
        return self._base.get_shift_row_index_for_user(telegram_id)

    def open_shift_for_user(self, telegram_id: int) -> int:
        return self._base.open_shift_for_user(telegram_id)

    def get_shift_summary(self, row: int) -> dict[str, object]:
        return self._base.get_shift_summary(row)

    # Работа со справочником рабочих ------------------------------------
    def list_active_workers(self) -> list[CrewWorker]:
        names = self._base.list_active_workers()
        return [CrewWorker(worker_id=index + 1, name=name) for index, name in enumerate(names)]

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
