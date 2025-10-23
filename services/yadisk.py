"""Клиент работы с Yandex Disk в режиме папки приложения."""

from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

_API_ROOT = "https://cloud-api.yandex.net/v1/disk"


@dataclass(slots=True)
class YandexDiskApiError(RuntimeError):
    """Исключение при обращении к API Yandex Disk."""

    status: int
    message: str
    details: str | None = None

    def __str__(self) -> str:  # pragma: no cover - читаемость
        base = f"Yandex Disk API error {self.status}: {self.message}"
        if self.details:
            return f"{base} ({self.details})"
        return base


class YandexDriveService:
    """Обёртка для REST API Яндекс.Диска в режиме app_folder."""

    def __init__(self, token: str, root: str) -> None:
        token = (token or "").strip()
        if not token:
            raise ValueError("Не задан OAuth-токен для Yandex Disk")
        root = (root or "").strip().strip("/")
        self._token = token
        self._root = root
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"OAuth {self._token}"})
        # гарантируем наличие корневой папки, если она указана
        if self._root:
            try:
                self.ensure_folder("")
            except YandexDiskApiError as exc:
                logger.error("Не удалось создать корневую папку на Yandex Disk: %s", exc)
                raise

    @property
    def root(self) -> str:
        return self._root

    def ensure_folder(self, relative_path: str) -> str:
        """Создаёт (при необходимости) каталог и возвращает его полный путь app:/..."""

        relative = (relative_path or "").strip().strip("/")
        segments: list[str] = []
        if self._root:
            segments.append(self._root)
        if relative:
            segments.extend(part for part in relative.split("/") if part)

        if not segments:
            full_path = "app:/"
            return full_path

        current_parts: list[str] = []
        for segment in segments:
            current_parts.append(segment)
            full_path = f"app:/{'/'.join(current_parts)}"
            self._ensure_single_folder(full_path)
        return f"app:/{'/'.join(segments)}"

    def upload_file(
        self,
        full_api_path: str,
        local_file: str,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Загружает файл по абсолютному пути app:/... и возвращает описание."""

        full_api_path = self._normalize_api_path(full_api_path)
        logger.debug("Запрос загрузки файла на %s", full_api_path)
        response = self._api(
            "get",
            "/resources/upload",
            params={"path": full_api_path, "overwrite": "true"},
        )
        href = response.json().get("href")
        if not href:
            raise YandexDiskApiError(response.status_code, "Не получена ссылка для загрузки")

        with open(local_file, "rb") as file_obj:
            upload_response = requests.put(
                href,
                data=file_obj,
                headers={"Content-Type": content_type},
                timeout=60,
            )
        if upload_response.status_code not in {201, 202, 204}:
            message = self._extract_error_message(upload_response)
            raise YandexDiskApiError(upload_response.status_code, message, upload_response.text)

        return {"path": full_api_path, "name": os.path.basename(full_api_path)}

    def _ensure_single_folder(self, full_api_path: str) -> None:
        response = self._api(
            "put",
            "/resources",
            params={"path": full_api_path},
            expected_status={201, 202, 409},
        )
        if response.status_code == 409:
            logger.debug("Папка %s уже существует", full_api_path)

    def _api(
        self,
        method: str,
        path: str,
        *,
        expected_status: Iterable[int] | None = None,
        **kwargs,
    ) -> requests.Response:
        url = f"{_API_ROOT}{path}"
        try:
            response = self._session.request(method, url, timeout=30, **kwargs)
        except requests.RequestException as exc:
            logger.error("Ошибка сети при обращении к Yandex Disk: %s", exc)
            raise YandexDiskApiError(0, "Network error", str(exc)) from exc

        expected = set(expected_status or {200})
        if response.status_code not in expected:
            message = self._extract_error_message(response)
            logger.error(
                "Ответ API %s %s: %s — %s", method.upper(), path, response.status_code, message
            )
            raise YandexDiskApiError(response.status_code, message, response.text)
        return response

    @staticmethod
    def _normalize_api_path(path: str) -> str:
        path = (path or "").strip()
        if not path.startswith("app:/"):
            raise ValueError("Ожидался путь вида app:/...")
        return path.replace("//", "/")

    @staticmethod
    def _extract_error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in ("message", "error", "description", "reason"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        if isinstance(payload, str) and payload:
            return payload
        return response.reason or "Неизвестная ошибка"
