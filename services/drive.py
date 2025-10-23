"""Обёртка для интеграции с Google Drive."""

from __future__ import annotations

import datetime as dt
import io
import logging
import os

try:  # pragma: no cover - сообщение при отсутствии зависимостей важно само по себе
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
except ModuleNotFoundError as exc:  # noqa: SIM105 - нужно перехватить все отсутствующие пакеты
    raise ModuleNotFoundError(
        "Не найдены зависимости Google API. Установите их командой "
        "pip install -r requirements.txt."
    ) from exc


SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"

logger = logging.getLogger(__name__)


class DriveService:
    """Клиент Google Drive, работающий от имени сервисного аккаунта."""

    def __init__(
        self,
        *,
        service_account_json_path: str | None = None,
        root_folder_id: str | None = None,
    ) -> None:
        path = service_account_json_path or os.environ.get("SERVICE_ACCOUNT_JSON_PATH")
        if not path:
            raise RuntimeError("SERVICE_ACCOUNT_JSON_PATH не задан: проверьте .env")

        credentials = Credentials.from_service_account_file(path, scopes=SCOPES)
        # discovery cache отключаем, чтобы не требовалось файловое хранилище
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.root_folder_id = root_folder_id or os.environ.get("DRIVE_ROOT_FOLDER_ID")
        if not self.root_folder_id:
            raise RuntimeError("DRIVE_ROOT_FOLDER_ID не задан: заполните переменную окружения")

    # ---- публичные методы ----
    def create_daily_folder(self, root_folder_id: str | None = None) -> str:
        """Возвращает ID папки с сегодняшней датой, создавая её при необходимости."""

        parent_id = root_folder_id or self.root_folder_id
        if not parent_id:
            raise RuntimeError("Не указан ID родительской папки Google Drive")

        folder_name = dt.date.today().isoformat()
        logger.info("Ищем папку за %s в родителе %s", folder_name, parent_id)

        query = (
            f"mimeType='{FOLDER_MIME}' and "
            f"name='{folder_name}' and "
            f"'{parent_id}' in parents and "
            "trashed = false"
        )

        try:
            response = (
                self.service.files()
                .list(q=query, spaces="drive", fields="files(id,name)", pageSize=1)
                .execute()
            )
        except Exception:  # noqa: BLE001 - хотим залогировать и пробросить исключение
            logger.error("Не удалось запросить список папок на Google Drive", exc_info=True)
            raise

        files = response.get("files", []) if response else []
        if files:
            folder_id = files[0]["id"]
            logger.info("Используем существующую папку: %s", folder_id)
            return folder_id

        metadata = {
            "name": folder_name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        }

        try:
            created = self.service.files().create(body=metadata, fields="id").execute()
        except Exception:  # noqa: BLE001 - логируем и пробрасываем
            logger.error("Не удалось создать папку %s на Google Drive", folder_name, exc_info=True)
            raise

        folder_id = created["id"]
        logger.info("Создана новая папка %s (id=%s)", folder_name, folder_id)
        return folder_id

    def upload_photo(self, folder_id: str, file: bytes, filename: str) -> str:
        """Загружает фото и возвращает ссылку на него."""

        media = MediaIoBaseUpload(io.BytesIO(file), mimetype="image/jpeg", resumable=False)
        metadata = {"name": filename, "parents": [folder_id]}

        try:
            created = (
                self.service.files()
                .create(body=metadata, media_body=media, fields="id,webViewLink")
                .execute()
            )
        except Exception:  # noqa: BLE001 - логируем и пробрасываем
            logger.error("Не удалось загрузить фото %s", filename, exc_info=True)
            raise

        file_id = created["id"]
        link = created.get("webViewLink") or ""

        if not link:
            try:
                info = self.service.files().get(fileId=file_id, fields="webViewLink").execute()
            except Exception:  # noqa: BLE001
                logger.error(
                    "Не удалось получить ссылку на фото %s (id=%s)", filename, file_id, exc_info=True
                )
                raise
            link = info.get("webViewLink", "")

        try:
            self.service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
        except Exception:  # noqa: BLE001 - доступ можно открыть вручную, поэтому не прерываем поток
            logger.info(
                "Не удалось автоматически открыть доступ по ссылке для файла %s", file_id, exc_info=True
            )

        logger.info("Фото %s загружено (id=%s)", filename, file_id)
        return link

