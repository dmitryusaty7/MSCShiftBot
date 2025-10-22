import os
import base64
import gspread
from google.oauth2.service_account import Credentials

# --- восстановление service_account.json ---
encoded = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")
if not encoded:
    raise RuntimeError("❌ Переменная GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 не найдена в окружении")

sa_path = "service_account.json"
if not os.path.exists(sa_path):
    data = base64.b64decode(encoded)
    with open(sa_path, "wb") as f:
        f.write(data)
    print("✅ service_account.json успешно восстановлен")
else:
    print("ℹ️ service_account.json уже существует")

# --- подключение к Google Sheets ---
sheet_id = os.getenv("SPREADSHEET_ID")
sheet_name = os.getenv("SHEET_DEFAULT", "Данные")

creds = Credentials.from_service_account_file(sa_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
client = gspread.authorize(creds)

sheet = client.open_by_key(sheet_id).worksheet(sheet_name)
sheet.append_row(["✅ Тестовая запись", "Codex OK"])
print(f"Запись добавлена в лист: {sheet_name}")
