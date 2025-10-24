"""Сценарий заполнения раздела «Материалы» со сбором фото."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from features.utils.messaging import safe_delete
from services.drive import get_drive
from services.drive_yadisk import YaDiskError, YaDiskService
from services.sheets import SheetsService

if TYPE_CHECKING:  # pragma: no cover
    from features.shift_menu import render_shift_menu as RenderShiftMenuFn

router = Router()
logger = logging.getLogger(__name__)
_sheets_service: SheetsService | None = None
_drive_service: YaDiskService | None = None


def _get_sheets_service() -> SheetsService:
    """Ленивая инициализация сервиса Google Sheets."""

    global _sheets_service
    if _sheets_service is None:
        _sheets_service = SheetsService()
    return _sheets_service


def _get_drive_service() -> YaDiskService:
    """Ленивая инициализация хранилища материалов."""

    global _drive_service
    if _drive_service is None:
        _drive_service = get_drive()
    return _drive_service


def _render_shift_menu(*args, **kwargs):
    """Ленивый импорт меню смены для избежания циклических зависимостей."""

    from features.shift_menu import render_shift_menu

    return render_shift_menu(*args, **kwargs)


BTN_BACK = "⬅ Назад"
BTN_HOME = "🏠 В меню"
BTN_SKIP = "Пропустить"
BTN_CONFIRM = "✅ Подтвердить"
BTN_DEL_LAST = "🗑 Удалить последнее"


def nav_kb(extra: list[str] | None = None) -> types.ReplyKeyboardMarkup:
    """Строит клавиатуру с базовой навигацией."""

    keyboard = ReplyKeyboardBuilder()
    if extra:
        for item in extra:
            keyboard.button(text=item)
        keyboard.adjust(len(extra))
    keyboard.button(text=BTN_BACK)
    keyboard.button(text=BTN_HOME)
    keyboard.adjust(2)
    return keyboard.as_markup(resize_keyboard=True)


def only_digits(text: str) -> int | None:
    """Возвращает число из строки или None, если формат неверный."""

    stripped = (text or "").strip()
    if stripped == BTN_SKIP:
        return 0
    return int(stripped) if re.fullmatch(r"\d+", stripped) else None


class MaterialsFSM(StatesGroup):
    """Состояния пошагового сценария заполнения материалов."""

    pvd = State()
    pvc = State()
    tape = State()
    photos = State()
    confirm = State()


@router.message(Command("materials"))
async def start_materials(message: types.Message, state: FSMContext) -> None:
    """Запускает процесс заполнения раздела материалов."""

    await safe_delete(message)
    user_id = message.from_user.id
    sheets = _get_sheets_service()
    row = await asyncio.to_thread(sheets.get_shift_row_index_for_user, user_id)
    if row is None:
        row = await asyncio.to_thread(sheets.open_shift_for_user, user_id)

    day_title = _format_day_title(dt.datetime.now().astimezone().date())

    try:
        drive = _get_drive_service()
        await asyncio.to_thread(drive.get_or_create_daily_folder, day_title)
    except (YaDiskError, RuntimeError, ValueError) as exc:
        logger.exception("Не удалось подготовить папку для материалов: %s", exc)
        await message.answer(
            "Не удалось подготовить хранилище материалов. Проверьте настройки токена "
            "и попробуйте позже или обратитесь к администратору."
        )
        await state.clear()
        await _render_shift_menu(message, user_id, row)
        return

    await state.update_data(
        user_id=user_id,
        row=row,
        photos=[],
        day_title=day_title,
    )
    await ask_pvd(message, state)


async def ask_pvd(message: types.Message, state: FSMContext) -> None:
    await state.set_state(MaterialsFSM.pvd)
    await message.answer(
        "укажите расход рулонов ПВД (в метрах).",
        reply_markup=nav_kb([BTN_SKIP]),
    )


@router.message(MaterialsFSM.pvd)
async def input_pvd(message: types.Message, state: FSMContext) -> None:
    if message.text in (BTN_BACK, BTN_HOME):
        return await exit_nav(message, state, message.text)
    value = only_digits(message.text or "")
    if value is None:
        return await message.answer("только цифры или «Пропустить».")
    await state.update_data(pvd=value)
    await ask_pvc(message, state)


async def ask_pvc(message: types.Message, state: FSMContext) -> None:
    await state.set_state(MaterialsFSM.pvc)
    await message.answer(
        "укажите расход трубок ПВХ (в штуках).",
        reply_markup=nav_kb([BTN_SKIP]),
    )


@router.message(MaterialsFSM.pvc)
async def input_pvc(message: types.Message, state: FSMContext) -> None:
    if message.text in (BTN_BACK, BTN_HOME):
        return await exit_nav(message, state, message.text)
    value = only_digits(message.text or "")
    if value is None:
        return await message.answer("только цифры или «Пропустить».")
    await state.update_data(pvc=value)
    await ask_tape(message, state)


async def ask_tape(message: types.Message, state: FSMContext) -> None:
    await state.set_state(MaterialsFSM.tape)
    await message.answer(
        "укажите расход клейкой ленты (в штуках).",
        reply_markup=nav_kb([BTN_SKIP]),
    )


@router.message(MaterialsFSM.tape)
async def input_tape(message: types.Message, state: FSMContext) -> None:
    if message.text in (BTN_BACK, BTN_HOME):
        return await exit_nav(message, state, message.text)
    value = only_digits(message.text or "")
    if value is None:
        return await message.answer("только цифры или «Пропустить».")
    await state.update_data(tape=value)
    await ask_photos_intro(message, state)


async def ask_photos_intro(message: types.Message, state: FSMContext) -> None:
    await state.set_state(MaterialsFSM.photos)
    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text=BTN_CONFIRM)
    keyboard.button(text=BTN_DEL_LAST)
    keyboard.adjust(2)
    keyboard.button(text=BTN_BACK)
    keyboard.button(text=BTN_HOME)
    keyboard.adjust(2, 2)
    await message.answer(
        "📸 Прикрепите фото крепления. Можно загрузить несколько файлов подряд.\n"
        f"После завершения нажмите «{BTN_CONFIRM}».",
        reply_markup=keyboard.as_markup(resize_keyboard=True),
    )


@router.message(MaterialsFSM.photos, F.photo)
async def on_photo(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[dict[str, str]] = data.get("photos", [])
    file_id = message.photo[-1].file_id
    time_label = _format_time_label(message.date)
    photos.append({"file_id": file_id, "time_label": time_label})
    await state.update_data(photos=photos)
    await message.answer(
        f"фото добавлено ({len(photos)} шт). можете прислать ещё или «{BTN_CONFIRM}»."
    )


@router.message(MaterialsFSM.photos, F.text == BTN_DEL_LAST)
async def del_last_photo(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[dict[str, str]] = data.get("photos", [])
    if photos:
        photos.pop()
        await state.update_data(photos=photos)
        await message.answer(f"последнее фото удалено. осталось: {len(photos)}.")
    else:
        await message.answer("список фото пуст.")


@router.message(MaterialsFSM.photos, F.text == BTN_CONFIRM)
async def confirm_upload(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data["user_id"]
    row = data["row"]
    photos: list[dict[str, str]] = data.get("photos", [])
    if not photos:
        await message.answer("Добавьте хотя бы одно фото перед подтверждением.")
        return

    sheets = _get_sheets_service()
    try:
        drive = _get_drive_service()
    except Exception as exc:  # pragma: no cover - зависит от окружения
        logger.exception("Не удалось инициализировать сервис хранения: %s", exc)
        await message.answer(
            "Не удалось подготовить хранилище материалов. Проверьте переменные окружения и повторите попытку."
        )
        return

    try:
        day_title: str = data["day_title"]
        saved_names: list[str] = []

        for index, entry in enumerate(photos, start=1):
            file_id = entry["file_id"]
            time_label = entry.get("time_label") or _format_time_label(dt.datetime.now())

            telegram_file = await message.bot.get_file(file_id)
            downloaded = await message.bot.download_file(telegram_file.file_path)
            content = _ensure_bytes(downloaded)

            ext = _normalize_extension(Path(telegram_file.file_path).suffix)
            mime = _guess_mime_type(ext)

            ordinal = index
            while True:
                candidate = f"{time_label}_{user_id}_{ordinal:02d}{ext}"
                try:
                    await asyncio.to_thread(
                        drive.save_photo,
                        content,
                        candidate,
                        day_title,
                        content_type=mime,
                    )
                except YaDiskError as exc:
                    if exc.status == 409:
                        ordinal += 1
                        continue
                    raise
                saved_names.append(candidate)
                break

        if not saved_names:
            raise RuntimeError("Не удалось сохранить фото в хранилище")

        public_url = await asyncio.to_thread(drive.folder_public_link, day_title)
        if not public_url:
            raise RuntimeError("Не удалось получить публичную ссылку на папку дня")

        await asyncio.to_thread(
            sheets.save_materials_block,
            row,
            pvd_m=data.get("pvd", 0),
            pvc_pcs=data.get("pvc", 0),
            tape_pcs=data.get("tape", 0),
            folder_link=public_url,
        )
        logger.info(
            "Материалы сохранены: user_id=%s, row=%s, фото=%s, ссылка=%s",
            user_id,
            row,
            len(saved_names),
            public_url,
        )
    except YaDiskError as exc:  # pragma: no cover - зависит от внешних сервисов
        logger.exception("Ошибка API Яндекс.Диска при сохранении материалов: %s", exc)
        if exc.status in {401, 403}:
            await message.answer(
                "Токен Яндекс-Диска недействителен или у приложения нет прав. "
                "Проверьте переменные окружения и токен в .env."
            )
        else:
            await message.answer(
                "Не удалось загрузить материалы в хранилище. Попробуйте позже или обратитесь к администратору."
            )
        return
    except Exception as exc:  # pragma: no cover - зависит от внешних сервисов
        logger.exception("Неизвестная ошибка при сохранении материалов: %s", exc)
        await message.answer(
            "Не удалось загрузить материалы в хранилище. Попробуйте позже или обратитесь к администратору."
        )
        return

    await message.answer("📎 фото успешно загружены. возвращаю в главное меню…")
    await state.clear()
    from features.main_menu import show_menu

    await show_menu(message)


@router.message(MaterialsFSM.photos)
async def photos_fallback(message: types.Message, state: FSMContext) -> None:
    if message.text in (BTN_BACK, BTN_HOME):
        return await exit_nav(message, state, message.text)
    await message.answer("пришлите фото или используйте кнопки ниже.")


async def exit_nav(message: types.Message, state: FSMContext, key: str) -> None:
    data = await state.get_data()
    await state.clear()
    if key == BTN_HOME:
        from features.main_menu import show_menu

        return await show_menu(message)
    await _render_shift_menu(message, data.get("user_id"), data.get("row"))


def _ensure_bytes(downloaded) -> bytes:
    try:
        if hasattr(downloaded, "read"):
            content = downloaded.read()
        else:
            content = downloaded
    finally:
        close = getattr(downloaded, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # pragma: no cover - best effort
                logger.debug("Не удалось закрыть поток загруженного файла", exc_info=True)
    if isinstance(content, str):
        content = content.encode()
    return bytes(content)


def _normalize_extension(suffix: str) -> str:
    suffix = (suffix or "").lower()
    if not suffix.startswith("."):
        suffix = f".{suffix}" if suffix else ""
    if suffix in {"", ".jpeg", ".jpg", ".jpe"}:
        return ".jpg"
    return suffix


def _guess_mime_type(ext: str) -> str:
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".heic": "image/heic",
    }
    return mapping.get(ext, "application/octet-stream")


def _format_day_title(day: dt.date) -> str:
    return f"Фотоотчет - {day:%d.%m.%Y}"


def _format_time_label(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    local = value.astimezone()
    return local.strftime("%H%M%S")
