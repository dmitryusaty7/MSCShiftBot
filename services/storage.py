"""Фабрика для выборки сервиса хранения материалов."""

from __future__ import annotations

import os

from services.env import require_env
from services.yadisk import YandexDriveService


def get_storage() -> YandexDriveService:
    """Возвращает настроенный сервис хранилища материалов."""

    provider = os.getenv("DRIVE_PROVIDER", "yadisk").lower()
    if provider != "yadisk":
        raise RuntimeError("Поддерживается только Yandex Disk (режим app_folder)")
    token = require_env("YADISK_OAUTH_TOKEN")
    root = os.getenv("YADISK_ROOT_FOLDER", "MSCShiftBot").strip().strip("/")
    return YandexDriveService(token=token, root=root)
