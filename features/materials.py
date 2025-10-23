"""Сценарий заполнения раздела «Материалы» со сбором фото."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import tempfile
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

    date_str = dt.date.today().strftime("%Y-%m-%d")

    try:
        drive = _get_drive_service()
        day_folder = await asyncio.to_thread(drive.make_date_folder, date_str)
        shift_folder = f"{day_folder}/row_{row}_uid_{user_id}"
        await asyncio.to_thread(drive.ensure_folder, shift_folder)
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
        photo_ids=[],
        date_str=date_str,
        shift_folder=shift_folder,
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
    photo_ids: list[str] = data.get("photo_ids", [])
    file_id = message.photo[-1].file_id
    photo_ids.append(file_id)
    await state.update_data(photo_ids=photo_ids)
    await message.answer(
        f"фото добавлено ({len(photo_ids)} шт). можете прислать ещё или «{BTN_CONFIRM}»."
    )


@router.message(MaterialsFSM.photos, F.text == BTN_DEL_LAST)
async def del_last_photo(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo_ids: list[str] = data.get("photo_ids", [])
    if photo_ids:
        photo_ids.pop()
        await state.update_data(photo_ids=photo_ids)
        await message.answer(f"последнее фото удалено. осталось: {len(photo_ids)}.")
    else:
        await message.answer("список фото пуст.")


@router.message(MaterialsFSM.photos, F.text == BTN_CONFIRM)
async def confirm_upload(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data["user_id"]
    row = data["row"]
    photo_ids: list[str] = data.get("photo_ids", [])
    if not photo_ids:
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
        shift_folder: str = data["shift_folder"]
        date_str: str = data["date_str"]
        uploaded: list[str] = []

        for index, file_id in enumerate(photo_ids, start=1):
            telegram_file = await message.bot.get_file(file_id)
            downloaded = await message.bot.download_file(telegram_file.file_path)
            tmp_file = _write_temp_file(downloaded)
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"row_{row}_uid_{user_id}_{timestamp}_{index:03d}.jpg"
            file_path = f"{shift_folder}/{filename}"
            try:
                info = await asyncio.to_thread(
                    drive.upload_file,
                    file_path,
                    tmp_file,
                    "image/jpeg",
                )
            finally:
                _safe_remove(tmp_file)
            uploaded.append(info.get("name", filename))

        if not uploaded:
            raise RuntimeError("Не удалось загрузить ни одного файла в хранилище")

        public_url = await asyncio.to_thread(drive.folder_public_link, date_str)
        if not public_url:
            raise RuntimeError("Публичная ссылка на папку дня не получена")

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
            len(photo_ids),
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

    await message.answer("Фото загружены. Ссылка добавлена в карточку смены.")
    await state.clear()
    await _render_shift_menu(message, user_id, row)


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


def _write_temp_file(downloaded) -> str:
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
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    try:
        tmp.write(content)
    finally:
        tmp.close()
    return tmp.name


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        logger.debug("Не удалось удалить временный файл %s", path)
