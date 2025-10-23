# mcsshiftbot

> telegram-бот для учёта смен, материалов и расходов проекта msc baltic  
> деплой в два шага, api на steroids, работает через google sheets + fsm core

---

## 🧩 what u need

- 🐍 python 3.11+
- 🔑 telegram bot token
- 📄 google sheets creds (service account with editor rights)
- 💻 os with bash / powershell support

---

## 🚀 setup env

```bash
# clone repo
$ git clone https://github.com/dmitryusaty7/mscshiftbot.git
$ cd mscshiftbot

# setup virtual env
$ python -m venv .venv

# activate env
$ source .venv/bin/activate     # linux / macos
$ .venv\scripts\activate        # windows

# install deps
$ pip install -r requirements.txt
```

put ur `service_account.json` to project root — this is ur google api key, no key = no life  
share ur sheet to service email with editor rights →  [google sheet link](https://docs.google.com/spreadsheets/d/1Hen1og8dtPl0L_zeBqSTZBXOpr0KJ0T2BKVbu5Ae2FM/edit)

then drop .env setup:

```bash
$ cp .env.example .env
$ nano .env
# fill ur bot token, sheet id, json path etc
```

---

## 💾 launch the bot

### smart way (auto env loader)

```bash
$ ./scripts/run_bot.sh
```
this script:
- auto loads .env
- activates venv
- validates creds
- runs `bot_shift.py`

if u see → `bot polling is started` — congrats, ur bot is alive 💀

---

### oldschool way

```bash
$ python bot_shift.py
```
then open telegram → find `@mscshiftbot` → hit `/start` → ur shift begins 🧩

---

## 🧭 check list

- go thru registration flow (fio → validation → status: active)
- create test shift → make sure sheet updates
- if bot dead → debug mode:

```bash
$ log_level=debug python bot_shift.py
```

- tweak sheet layout inside `src/sheets_service.py` if needed

---

## 🧠 pro tips

- never commit ur `service_account.json` (keep in .gitignore)
- if google api rate limits u → chill, retry later or up quota
- ready for webapp mini integration via telegram sdk — backend is modular

---

💬 mcs shiftbot — как админка для бригад, но через telegram и без боли.  
run `/start`, register ur crew, drop expenses, log shifts. ez.
