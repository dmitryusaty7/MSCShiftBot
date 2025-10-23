# MCSShiftBot

telegram-бот для учёта смен, материалов и расходов

---

## требования

- python 3.11+
- telegram bot token
- service account json для google sheets с правами editor
- доступ к целевой google sheet (id в .env)
- OAuth-токен Yandex Disk с правами на папку приложения

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
скопируй `.env.example` → `.env` и заполни: `BOT_TOKEN`, `SPREADSHEET_ID`, `SERVICE_ACCOUNT_JSON_PATH` (можно оставить старое имя `GOOGLE_SERVICE_ACCOUNT_JSON_PATH`, но рекомендуется перейти на новое), а также параметры `DRIVE_PROVIDER`, `YADISK_OAUTH_TOKEN`, `YADISK_ROOT_FOLDER`.

---

## запуск

вариант a — автоматом (рекоммендовано):

```bash
./scripts/run_bot.sh
```

скрипт `scripts/run_bot.sh` рассчитан на bash-окружение (git bash, wsl, macos, linux).

вариант b — вручную:

```bash
python bot_shift.py
```

### windows launch

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bot.ps1
```

скрипт `scripts/run_bot.ps1` предназначен для Windows PowerShell.

в консоли должно появиться: `bot polling is started` — значит live
в telegram: @mscshiftbot → /start

---

## что делает этот репо

- fsm-регистрация пользователей (фио, валидация, дубликаты)
- запись данных в sheet: лист `дaнные` (диапазон a:g)
- отдельные модули для расходов, материалов, состава бригады
- sheets_service — единый интерфейс к google sheets

---

## хранилище материалов

Фотографии материалов сохраняются в пользовательской области Яндекс.Диска. Для каждой смены создаётся структура `disk:/MSCShiftBot/<YYYY-MM-DD>/row_<row>_uid_<user>/…`, папка дня автоматически публикуется, а публичная ссылка записывается в Google Sheets.

1. Получите OAuth-токен с правами `cloud_api:disk.write` (и при необходимости `cloud_api:disk.read`). Самый быстрый способ — перейти по ссылке `https://oauth.yandex.ru/authorize?response_type=token&client_id=<ваш_client_id>` и выдать права выбранному приложению.
2. В `.env` задайте `DRIVE_PROVIDER=yadisk`, `YADISK_OAUTH_TOKEN=<токен>`, при желании измените корневую папку через `YADISK_ROOT_FOLDER` (по умолчанию `/MSCShiftBot`) и оставьте `YADISK_PUBLISH=true`, чтобы бот публиковал папку дня.
3. После подтверждения пользователем бот загружает файлы, публикует папку текущей даты и сохраняет `public_url` в столбец материалов. Ссылку можно открыть без авторизации.

При ошибках загрузки убедитесь, что токен активен и у приложения остались необходимые права на операции с диском.

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

