import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

BOT_TOKEN = "<твой_токен_от_BotFather>"
SPREADSHEET_ID = "1Hen1og8dtPl0L_zeBqSTZBXOpr0KJ0T2BKVbu5Ae2FM"
SERVICE_ACCOUNT_JSON_PATH = "./service_account.json"

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_client():
    with open(SERVICE_ACCOUNT_JSON_PATH, "r", encoding="utf-8") as f:
        info = json.load(f)
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer("Привет 👋\nЯ бот учёта смен MSCBaltic\nКоманда: /shift_new")

@dp.message(Command("shift_new"))
async def shift_new(message: types.Message):
    try:
        gc = get_client()
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet("Расходы смены")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws.append_row([message.from_user.username, "Shift from bot", now])
        await message.answer("✅ Смена успешно добавлена!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
