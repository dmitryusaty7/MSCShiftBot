"""REST-клиент Яндекс.Диска для хранения материалов."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Iterable

import requests

__all__ = ["YaDiskError", "YaDiskService"]

logger = logging.getLogger(__name__)

_API_ROOT = "https://cloud-api.yandex.net/v1/disk"


class YaDiskError(RuntimeError):
    """Ошибка при обращении к API Яндекс.Диска."""

    def __init__(self, status: int, message: str, details: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details

    def __str__(self) -> str:  # pragma: no cover - человекочитаемое сообщение
        base = f"Yandex Disk API error {self.status}: {self.message}"
        if self.details:
            return f"{base} ({self.details})"
        return base


class YaDiskService:
    """Клиент работы с пользовательской областью Яндекс.Диска."""

    def __init__(self, token: str, root: str = "/MSCShiftBot", publish: bool = True) -> None:
        token = (token or "").strip()
        if not token:
            raise ValueError("Не задан OAuth-токен Яндекс.Диска")

        self._root = self._clean_path(root or "/MSCShiftBot")
        self._publish = bool(publish)
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"OAuth {token}"})

        try:
            self.ensure_folder(self._root)
        except YaDiskError:
            logger.exception("Не удалось подготовить корневую папку на Яндекс.Диске")
            raise

    # --- публичные методы -------------------------------------------------

    def ensure_folder(self, path: str) -> None:
        """Гарантирует наличие папки (игнорируя 409 «уже существует»)."""

        absolute = self._absolute_path(path)
        segments = [segment for segment in absolute.strip("/").split("/") if segment]
        current: list[str] = []
        for segment in segments:
            current.append(segment)
            self._ensure_single_folder(self._to_disk_path("/" + "/".join(current)))

    def upload_file(
        self,
        dst_path: str,
        file_path: str | Path,
        *,
        content_type: str = "application/octet-stream",
        overwrite: bool = False,
    ) -> dict:
        """Загружает файл и возвращает базовые метаданные."""

        absolute_path = self._absolute_path(dst_path)
        disk_path = self._to_disk_path(absolute_path)
        response = self._request(
            "get",
            "/resources/upload",
            params={"path": disk_path, "overwrite": "true" if overwrite else "false"},
        )
        payload = self._safe_json(response)
        href = payload.get("href") if isinstance(payload, dict) else None
        if not href:
            raise YaDiskError(response.status_code, "Не получена ссылка загрузки", response.text)

        file_obj = Path(file_path)
        with file_obj.open("rb") as handle:
            upload = requests.put(
                href,
                data=handle,
                headers={"Content-Type": content_type},
                timeout=120,
            )
        if upload.status_code not in {201, 202, 204}:
            message = self._extract_error_message(upload)
            raise YaDiskError(upload.status_code, message, upload.text)

        return {"path": absolute_path, "name": file_obj.name}

    def publish_folder(self, folder_path: str) -> str:
        """Публикует папку и возвращает `public_url`."""

        absolute = self._absolute_path(folder_path)
        disk_path = self._to_disk_path(absolute)
        response = self._request(
            "put",
            "/resources/publish",
            params={"path": disk_path},
            allow={200, 202, 409},
        )
        if response.status_code == 409:
            logger.info("Папка %s уже опубликована", absolute)
        metadata = self._request(
            "get",
            "/resources",
            params={"path": disk_path, "fields": "public_url"},
        )
        info = self._safe_json(metadata)
        public_url = info.get("public_url") if isinstance(info, dict) else None
        if not public_url:
            raise YaDiskError(metadata.status_code, "Не удалось получить public_url", metadata.text)
        return public_url

    def get_or_create_daily_folder(self, title: str) -> str:
        """Создаёт (при необходимости) и возвращает абсолютный путь к папке дня."""

        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Не указано название папки дня")
        absolute = self._absolute_path(f"{self._root}/{clean_title}")
        self.ensure_folder(absolute)
        return absolute

    def save_photo(
        self,
        data: bytes,
        filename: str,
        day_title: str,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Сохраняет фото в дневной папке и возвращает имя сохранённого файла."""

        folder_path = self.get_or_create_daily_folder(day_title)
        target_path = f"{folder_path}/{filename}"
        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            tmp.write(data)
            tmp.flush()
        finally:
            tmp.close()

        try:
            self.upload_file(target_path, tmp.name, content_type=content_type, overwrite=False)
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                logger.debug("Не удалось удалить временный файл %s", tmp.name)

        return filename

    def folder_public_link(self, title_or_path: str) -> str:
        """Возвращает public_url для папки, публикуя её при необходимости."""

        value = (title_or_path or "").strip()
        if not value:
            raise ValueError("Не указана папка для публикации")

        if value.startswith("/"):
            absolute = self._absolute_path(value)
        else:
            absolute = self.get_or_create_daily_folder(value)

        if not self._publish:
            metadata = self._request(
                "get",
                "/resources",
                params={"path": self._to_disk_path(absolute), "fields": "public_url"},
            )
            info = self._safe_json(metadata)
            return info.get("public_url") or ""

        return self.publish_folder(absolute)

    # --- внутренние помощники --------------------------------------------

    def _ensure_single_folder(self, disk_path: str) -> None:
        response = self._request(
            "put",
            "/resources",
            params={"path": disk_path},
            allow={201, 202, 409},
        )
        if response.status_code == 409:
            logger.debug("Папка %s уже существует", disk_path)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        allow: Iterable[int] | None = None,
        retries: int = 3,
        **kwargs,
    ) -> requests.Response:
        url = f"{_API_ROOT}{path}"
        allowed = {int(code) for code in (allow or {200})}
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    timeout=30,
                    **kwargs,
                )
            except requests.RequestException as exc:  # noqa: PERF203 - повторяем попытки
                if attempt >= retries:
                    raise YaDiskError(0, "Сетевая ошибка", str(exc)) from exc
                wait = 2 ** (attempt - 1)
                logger.warning("Ошибка сети %s %s: %s. Повтор через %s с", method.upper(), url, exc, wait)
                time.sleep(wait)
                continue

            if response.status_code in allowed:
                return response

            if response.status_code in {429} or response.status_code >= 500:
                if attempt < retries:
                    wait = 2 ** (attempt - 1)
                    logger.warning(
                        "API %s %s вернул %s. Повтор через %s с", method.upper(), url, response.status_code, wait
                    )
                    time.sleep(wait)
                    continue

            message = self._extract_error_message(response)
            raise YaDiskError(response.status_code, message, response.text)

    def _absolute_path(self, path: str | None) -> str:
        if not path:
            return self._root
        cleaned = self._clean_path(path)
        if cleaned.startswith(self._root):
            return cleaned
        return self._clean_path(f"{self._root}/{cleaned.lstrip('/')}")

    @staticmethod
    def _clean_path(path: str) -> str:
        value = (path or "").strip()
        if value.startswith("disk:/"):
            value = value[len("disk:") :]
        if not value.startswith("/"):
            value = f"/{value}"
        while "//" in value:
            value = value.replace("//", "/")
        if len(value) > 1 and value.endswith("/"):
            value = value[:-1]
        return value or "/"

    @staticmethod
    def _to_disk_path(absolute: str) -> str:
        cleaned = YaDiskService._clean_path(absolute)
        return f"disk:{cleaned}"

    @staticmethod
    def _safe_json(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _extract_error_message(response: requests.Response) -> str:
        payload = None
        try:
            payload = response.json()
        except ValueError:
            pass
        if isinstance(payload, dict):
            for key in ("message", "error", "description", "reason"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        if isinstance(payload, str) and payload:
            return payload
        return response.reason or "Неизвестная ошибка"
