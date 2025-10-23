from __future__ import annotations

import os

from dotenv import load_dotenv

from .sheets_service import SheetsService

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8276005298:AAEHrKe_dJuU__H_Lz_br7vvaBAl_OfmN7w")
SPREADSHEET_ID = os.getenv(
    "SPREADSHEET_ID", "1Hen1og8dtPl0L_zeBqSTZBXOpr0KJ0T2BKVbu5Ae2FM"
)
SERVICE_ACCOUNT_JSON_PATH = os.getenv(
    "SERVICE_ACCOUNT_JSON_PATH", "./service_account.json"
)

sheets_service = SheetsService(
    spreadsheet_id=SPREADSHEET_ID,
    service_account_path=SERVICE_ACCOUNT_JSON_PATH,
)

__all__ = [
    "BOT_TOKEN",
    "SPREADSHEET_ID",
    "SERVICE_ACCOUNT_JSON_PATH",
    "sheets_service",
]
