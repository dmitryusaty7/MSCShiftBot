import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# === НАСТРОЙКИ ===
SPREADSHEET_ID = "1Hen1og8dtPl0L_zeBqSTZBXOpr0KJ0T2BKVbu5Ae2FM"
SERVICE_ACCOUNT_JSON_PATH = "./service_account.json"
SHEET_NAME = "Данные"
# ==================

def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    with open(SERVICE_ACCOUNT_JSON_PATH, "r", encoding="utf-8") as f:
        info = json.load(f)
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def append_test_row():
    gc = get_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws.append_row(["✅ Тестовая запись", now, "Python OK"])
    print("✅ Успешно записано:", now)

if __name__ == "__main__":
    append_test_row()
