"""Фабрика выбора сервиса хранилища материалов."""

from __future__ import annotations

import os

from services.drive_yadisk import YaDiskService
from services.env import require_env

__all__ = ["get_drive"]


def get_drive() -> YaDiskService:
    """Возвращает настроенный сервис хранения материалов."""

    provider = os.getenv("DRIVE_PROVIDER", "yadisk").strip().lower()
    if provider != "yadisk":
        raise RuntimeError("Поддерживается только провайдер 'yadisk'")

    token = require_env("YADISK_OAUTH_TOKEN")
    root = os.getenv("YADISK_ROOT_FOLDER", "/MSCShiftBot") or "/MSCShiftBot"
    publish_flag = os.getenv("YADISK_PUBLISH", "true").strip().lower() == "true"
    return YaDiskService(token=token, root=root, publish=publish_flag)
