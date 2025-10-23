# MCSShiftBot

telegram-бот для учёта смен, материалов и расходов

---

## требования

- python 3.11+
- telegram bot token
- service account json для google sheets с правами editor
- доступ к целевой google sheet (id в .env)

---

## подготовка (быстро)

```bash
# клонируешь и заходишь в репу
git clone https://github.com/dmitryusaty7/mscshiftbot.git
cd mscshiftbot

# создаёшь виртуалку
python -m venv .venv

# активируешь
# linux/mac
source .venv/bin/activate
# windows (powershell)
.venv\scripts\activate

# ставишь зависимости
python -m pip install -r requirements.txt
```

помести `service_account.json` в корень проекта. не коммить файл. добавь в .gitignore.
скопируй `.env.example` → `.env` и заполни: `bot_token`, `spreadsheet_id`, `service_account_json_path`.

---

## запуск

вариант a — автоматом (рекоммендовано):

```bash
./scripts/run_bot.sh
```

вариант b — вручную:

```bash
python bot_shift.py
```

в консоли должно появиться: `bot polling is started` — значит live
в telegram: @mscshiftbot → /start

---

## что делает этот репо

- fsm-регистрация пользователей (фио, валидация, дубликаты)
- запись данных в sheet: лист `дaнные` (диапазон a:g)
- отдельные модули для расходов, материалов, состава бригады
- sheets_service — единый интерфейс к google sheets

---

## правила регистрации (для разработчика)

- проверять только колонку a (telegram id) при поиске строки
- записывать данные последовательно в первую пустую a
- статус в колонке g = "активен" по умолчанию
- если в g стоит "архив" — reject, показать сообщение о блоке
- не трогать колонки h:m
- нормализовать фио: первая буква заглавная, остальные строчные
- запрещать цифры и спецсимволы в фио
- проверять дубликат по полному фио среди записей с g == "активен"

---

## тесты и дебаг

- локально: `log_level=debug python bot_shift.py`
- unit-tests: покрыть sheets_service (mocks для gspread)
- ручной чек: регистрация → запись a:g → создание тестовой смены

---

## заметки по деплою

- не хранить `service_account.json` в репо
- использовать env-vars в CI (CI secret: SERVICE_ACCOUNT_JSON_BASE64)
- при деплое: раскодировать base64 → временный json, использовать Credentials.from_service_account_info

---

## quick commands

```bash
# обновить main из remote
git checkout main && git pull origin main

# собрать окружение и запустить
python -m venv .venv && source .venv/bin/activate && python -m pip install -r requirements.txt && python bot_shift.py
```

