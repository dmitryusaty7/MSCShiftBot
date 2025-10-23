"""Сервис для работы с Google Drive через сервисный аккаунт."""

from __future__ import annotations

import io
import os
from typing import Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class DriveService:
    """Обёртка над Google Drive API для базовых операций с файлами."""

    def __init__(
        self,
        service_account_json_path: str | None = None,
        root_folder_id: str | None = None,
    ) -> None:
        path = service_account_json_path or os.environ.get("SERVICE_ACCOUNT_JSON_PATH")
        if not path:
            raise RuntimeError("SERVICE_ACCOUNT_JSON_PATH не задан")

        credentials = Credentials.from_service_account_file(path, scopes=SCOPES)
        self._drive = build("drive", "v3", credentials=credentials)
        self.root_folder_id = root_folder_id or os.environ.get("DRIVE_ROOT_FOLDER_ID")

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Создаёт папку и возвращает её идентификатор."""

        metadata: dict[str, object] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        parent = parent_id or self.root_folder_id
        if parent:
            metadata["parents"] = [parent]

        file = self._drive.files().create(body=metadata, fields="id").execute()
        return file["id"]

    def upload_bytes(
        self,
        parent_id: str,
        filename: str,
        data: bytes,
        mime: str = "image/jpeg",
    ) -> str:
        """Загружает файл из байтов в указанную папку."""

        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
        metadata = {"name": filename, "parents": [parent_id]}
        file = (
            self._drive.files()
            .create(body=metadata, media_body=media, fields="id")
            .execute()
        )
        return file["id"]

    def set_anyone_reader(self, file_or_folder_id: str) -> None:
        """Открывает доступ по ссылке для чтения."""

        body = {"type": "anyone", "role": "reader"}
        self._drive.permissions().create(fileId=file_or_folder_id, body=body).execute()

    def web_link(self, file_or_folder_id: str) -> str:
        """Возвращает ссылку для просмотра файла или папки."""

        file = (
            self._drive.files()
            .get(fileId=file_or_folder_id, fields="webViewLink")
            .execute()
        )
        return file.get("webViewLink")
