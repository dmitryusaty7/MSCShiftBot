"""Сценарий раздела «Материалы» с очисткой истории сообщений и фото."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Dict, List

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards.materials import (
    CONFIRM_BUTTON,
    DELETE_LAST_BUTTON,
    EDIT_BUTTON,
    MENU_BUTTON,
    SKIP_BUTTON,
    START_MATERIALS_BUTTON,
    materials_amount_keyboard,
    materials_confirm_keyboard,
    materials_photos_keyboard,
    materials_remove_keyboard,
    materials_start_keyboard,
)
from bot.utils.cleanup import cleanup_screen, remember_message, send_screen_message
from bot.utils.flash import flash_message
from bot.validators.number import parse_amount
from features.utils.messaging import safe_delete
from services.drive import get_drive
from services.drive_yadisk import YaDiskError, YaDiskService
from services.sheets import SheetsService

router = Router(name="materials")

logger = logging.getLogger(__name__)

_service: SheetsService | None = None
_drive: YaDiskService | None = None


class MaterialsState(StatesGroup):
    """Этапы сценария раздела «Материалы»."""

    INTRO = State()
    PVD = State()
    PVC = State()
    TAPE = State()
    PHOTOS = State()
    CONFIRM = State()


def _get_service() -> SheetsService:
    """Возвращает экземпляр сервиса таблиц."""

    global _service
    if _service is None:
        _service = SheetsService()
    return _service


def _get_drive() -> YaDiskService:
    """Ленивая инициализация доступа к Яндекс.Диску."""

    global _drive
    if _drive is None:
        _drive = get_drive()
    return _drive


def _make_tracker() -> Dict[str, Any]:
    """Создаёт структуру для отслеживания сообщений шага."""

    return {"prompt_id": None, "user_messages": [], "bot_messages": []}


async def _get_context(state: FSMContext) -> Dict[str, Any]:
    """Возвращает контекст раздела из FSM."""

    data = await state.get_data()
    context = data.get("materials_ctx")
    if not isinstance(context, dict):
        context = {}
    tracker = context.get("tracker")
    if not isinstance(tracker, dict):
        tracker = _make_tracker()
    else:
        tracker.setdefault("prompt_id", None)
        tracker.setdefault("user_messages", [])
        tracker.setdefault("bot_messages", [])
    context["tracker"] = tracker
    return context


async def _save_context(state: FSMContext, context: Dict[str, Any]) -> None:
    """Сохраняет контекст раздела в FSM."""

    await state.update_data(materials_ctx=context)


async def _set_prompt(
    message: types.Message,
    state: FSMContext,
    *,
    prompt: types.Message,
) -> None:
    """Регистрирует новое сообщение-приглашение."""

    context = await _get_context(state)
    tracker = _make_tracker()
    tracker["prompt_id"] = prompt.message_id
    context["tracker"] = tracker
    await _save_context(state, context)
    remember_message(message.chat.id, prompt.message_id)


async def _add_user_message(state: FSMContext, message_id: int, chat_id: int) -> None:
    """Добавляет сообщение пользователя в очередь очистки."""

    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    tracker.setdefault("user_messages", []).append(message_id)
    context["tracker"] = tracker
    await _save_context(state, context)
    remember_message(chat_id, message_id)


async def _add_bot_message(state: FSMContext, message_id: int, chat_id: int) -> None:
    """Добавляет вспомогательное сообщение бота для последующего удаления."""

    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    tracker.setdefault("bot_messages", []).append(message_id)
    context["tracker"] = tracker
    await _save_context(state, context)
    remember_message(chat_id, message_id)


async def _delete_messages(bot: types.Bot, chat_id: int, message_ids: List[int]) -> None:
    """Безопасно удаляет набор сообщений."""

    for message_id in message_ids:
        if not message_id:
            continue
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            logger.debug("Сообщение %s уже удалено", message_id)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось удалить сообщение %s", message_id)


async def _cleanup_step(message: types.Message, state: FSMContext) -> None:
    """Удаляет сообщения текущего шага и сбрасывает трекер."""

    context = await _get_context(state)
    tracker = context.get("tracker", _make_tracker())
    chat_id = message.chat.id
    bot = message.bot
    prompt_id = tracker.get("prompt_id")
    bot_messages = tracker.get("bot_messages", [])
    user_messages = tracker.get("user_messages", [])
    ids: List[int] = []
    if prompt_id:
        ids.append(prompt_id)
    ids.extend(bot_messages)
    await _delete_messages(bot, chat_id, ids)
    await _delete_messages(bot, chat_id, user_messages)
    context["tracker"] = _make_tracker()
    await _save_context(state, context)


def _format_day_title(day: dt.date) -> str:
    """Возвращает название папки по дате смены."""

    return day.strftime("%Y-%m-%d")


def _format_time_label(moment: dt.datetime) -> str:
    """Формирует временную метку для имени файла."""

    return moment.astimezone().strftime("%H%M%S")


def _normalize_extension(suffix: str) -> str:
    """Возвращает расширение файла в формате .jpg/.png и т.п."""

    clean = suffix.lower().strip()
    return clean if clean else ".jpg"


def _guess_mime_type(ext: str) -> str:
    """Определяет MIME-тип загружаемого фото по расширению."""

    mapping = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    return mapping.get(ext, "application/octet-stream")


def _ensure_bytes(downloaded: Any) -> bytes:
    """Приводит скачанный файл к ``bytes``."""

    try:
        if hasattr(downloaded, "read"):
            return downloaded.read()
        return downloaded
    finally:
        closer = getattr(downloaded, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось закрыть файловый объект", exc_info=True)


async def _return_to_menu(message: types.Message, state: FSMContext) -> None:
    """Возвращает пользователя в меню смены без сохранения."""

    context = await _get_context(state)
    user_id = context.get("user_id")
    row = context.get("row")
    await _cleanup_step(message, state)
    await cleanup_screen(message.bot, message.chat.id, keep_start=False)
    await state.update_data(materials_ctx=None)
    if not isinstance(user_id, int) or not isinstance(row, int):
        return
    from bot.handlers.shift_menu import render_shift_menu

    await render_shift_menu(
        message,
        user_id,
        row,
        state=state,
        service=_get_service(),
        delete_trigger_message=False,
        show_progress=False,
    )


async def start_materials(
    message: types.Message,
    state: FSMContext,
    *,
    user_id: int | None = None,
) -> None:
    """Точка входа в раздел «Материалы» из меню смены."""

    await safe_delete(message)
    await cleanup_screen(message.bot, message.chat.id, keep_start=True)

    actual_user_id = user_id or (message.from_user.id if message.from_user else None)
    if actual_user_id is None:
        await message.answer(
            "Не удалось определить пользователя. Начните смену заново через главное меню."
        )
        await state.update_data(materials_ctx=None)
        return

    service = _get_service()
    row = await asyncio.to_thread(service.get_shift_row_index_for_user, actual_user_id)
    if row is None:
        row = await asyncio.to_thread(service.open_shift_for_user, actual_user_id)

    today = dt.datetime.now().astimezone().date()
    day_title = _format_day_title(today)

    try:
        drive = _get_drive()
        await asyncio.to_thread(drive.get_or_create_daily_folder, day_title)
    except (YaDiskError, RuntimeError, ValueError) as exc:
        logger.exception("Не удалось подготовить папку материалов: %s", exc)
        await message.answer(
            "Не удалось подготовить хранилище материалов. Попробуйте позже или обратитесь к администратору."
        )
        await state.update_data(materials_ctx=None)
        return

    intro_lines = [
        "📦 Материалы — ввод данных",
        "",
        "Заполняем расход плёнки ПВД, трубок ПВХ и клейкой ленты по текущей смене.",
        "При необходимости прикрепите фото чеков или крепления перед подтверждением.",
    ]
    prompt = await send_screen_message(
        message,
        "\n".join(intro_lines),
        reply_markup=materials_start_keyboard(),
    )

    context = {
        "user_id": actual_user_id,
        "row": row,
        "day_title": day_title,
        "data": {},
        "photos": [],
        "tracker": {"prompt_id": prompt.message_id, "user_messages": [], "bot_messages": []},
    }
    await _save_context(state, context)
    await state.set_state(MaterialsState.INTRO)


async def _ask_pvd(message: types.Message, state: FSMContext) -> None:
    """Запрашивает расход рулонов ПВД."""

    prompt = await message.answer(
        "Укажите расход рулонов ПВД (в метрах).",
        reply_markup=materials_amount_keyboard(include_skip=True),
    )
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(MaterialsState.PVD)


async def _ask_pvc(message: types.Message, state: FSMContext) -> None:
    """Переходит к вводу трубок ПВХ."""

    prompt = await message.answer(
        "Укажите расход трубок ПВХ (в штуках).",
        reply_markup=materials_amount_keyboard(include_skip=True),
    )
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(MaterialsState.PVC)


async def _ask_tape(message: types.Message, state: FSMContext) -> None:
    """Переходит к вводу расхода клейкой ленты."""

    prompt = await message.answer(
        "Укажите расход клейкой ленты (в штуках).",
        reply_markup=materials_amount_keyboard(include_skip=True),
    )
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(MaterialsState.TAPE)


async def _ask_photos(message: types.Message, state: FSMContext) -> None:
    """Предлагает загрузить фото крепления."""

    prompt = await message.answer(
        "📸 Прикрепите фото крепления. Можно отправить несколько файлов подряд.\n"
        "После завершения нажмите «✅ Подтвердить».",
        reply_markup=materials_photos_keyboard(),
    )
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(MaterialsState.PHOTOS)


async def _ask_confirm(message: types.Message, state: FSMContext) -> None:
    """Формирует итоговое сообщение с проверкой данных."""

    context = await _get_context(state)
    data = context.get("data", {})
    photos: list[dict[str, Any]] = context.get("photos", [])
    text = (
        "Проверьте введённые данные:\n"
        f"• ПВД (м): {data.get('pvd', 0)}\n"
        f"• ПВХ (шт): {data.get('pvc', 0)}\n"
        f"• Лента (шт): {data.get('tape', 0)}\n"
        f"• Фото: {len(photos)} шт"
    )
    prompt = await message.answer(text, reply_markup=materials_confirm_keyboard())
    await _set_prompt(message, state, prompt=prompt)
    await state.set_state(MaterialsState.CONFIRM)


async def _handle_menu_button(message: types.Message, state: FSMContext) -> bool:
    """Обрабатывает кнопку выхода в меню смены."""

    if message.text == MENU_BUTTON:
        await _add_user_message(state, message.message_id, message.chat.id)
        await _return_to_menu(message, state)
        return True
    return False


@router.message(MaterialsState.INTRO)
async def handle_intro(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает стартовый экран раздела."""

    if await _handle_menu_button(message, state):
        return

    if message.text != START_MATERIALS_BUTTON:
        reply = await message.answer("Используйте кнопку «📦 Начать ввод материалов».")
        await _add_user_message(state, message.message_id, message.chat.id)
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    await _add_user_message(state, message.message_id, message.chat.id)
    await _cleanup_step(message, state)
    await _ask_pvd(message, state)


@router.message(MaterialsState.PVD)
async def handle_pvd(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает расход ПВД."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id, message.chat.id)
    try:
        amount = parse_amount(message.text or "", skip_token=SKIP_BUTTON)
    except ValueError as exc:
        reply = await message.answer(str(exc))
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    context = await _get_context(state)
    context.setdefault("data", {})["pvd"] = amount
    await _save_context(state, context)
    await _cleanup_step(message, state)
    await _ask_pvc(message, state)


@router.message(MaterialsState.PVC)
async def handle_pvc(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает расход трубок ПВХ."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id, message.chat.id)
    try:
        amount = parse_amount(message.text or "", skip_token=SKIP_BUTTON)
    except ValueError as exc:
        reply = await message.answer(str(exc))
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    context = await _get_context(state)
    context.setdefault("data", {})["pvc"] = amount
    await _save_context(state, context)
    await _cleanup_step(message, state)
    await _ask_tape(message, state)


@router.message(MaterialsState.TAPE)
async def handle_tape(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает расход клейкой ленты."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id, message.chat.id)
    try:
        amount = parse_amount(message.text or "", skip_token=SKIP_BUTTON)
    except ValueError as exc:
        reply = await message.answer(str(exc))
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    context = await _get_context(state)
    context.setdefault("data", {})["tape"] = amount
    await _save_context(state, context)
    await _cleanup_step(message, state)
    await _ask_photos(message, state)


@router.message(MaterialsState.PHOTOS, F.photo)
async def handle_photo(message: types.Message, state: FSMContext) -> None:
    """Добавляет фото к списку материалов."""

    await _add_user_message(state, message.message_id, message.chat.id)
    context = await _get_context(state)
    photos: list[dict[str, Any]] = context.setdefault("photos", [])
    file_id = message.photo[-1].file_id
    time_label = _format_time_label(message.date or dt.datetime.now())
    photos.append({"file_id": file_id, "time_label": time_label})
    await _save_context(state, context)

    reply = await message.answer(
        f"Фото добавлено. Всего: {len(photos)}. Можете загрузить ещё или подтвердить."
    )
    await _add_bot_message(state, reply.message_id, message.chat.id)


@router.message(MaterialsState.PHOTOS, F.text == DELETE_LAST_BUTTON)
async def handle_delete_last(message: types.Message, state: FSMContext) -> None:
    """Удаляет последнее загруженное фото."""

    await _add_user_message(state, message.message_id, message.chat.id)
    context = await _get_context(state)
    photos: list[dict[str, Any]] = context.setdefault("photos", [])
    if photos:
        photos.pop()
        reply = await message.answer(f"Последнее фото удалено. Осталось: {len(photos)}.")
    else:
        reply = await message.answer("Список фото пуст — добавьте файл перед подтверждением.")
    await _add_bot_message(state, reply.message_id, message.chat.id)
    await _save_context(state, context)


@router.message(MaterialsState.PHOTOS)
async def handle_photos_controls(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает подтверждение фото или возврат."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id, message.chat.id)
    text = (message.text or "").strip()
    if text != CONFIRM_BUTTON:
        reply = await message.answer("Используйте кнопки подтверждения на клавиатуре.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    context = await _get_context(state)
    photos: list[dict[str, Any]] = context.get("photos", [])
    if not photos:
        reply = await message.answer("Добавьте хотя бы одно фото перед подтверждением.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    await _cleanup_step(message, state)
    await _ask_confirm(message, state)


async def _save_materials(context: Dict[str, Any], message: types.Message) -> str:
    """Сохраняет данные раздела и возвращает публичную ссылку на папку."""

    user_id = context.get("user_id")
    row = context.get("row")
    day_title = context.get("day_title")
    photos: list[dict[str, Any]] = context.get("photos", [])
    data = context.get("data", {})

    if not isinstance(user_id, int) or not isinstance(row, int):
        raise RuntimeError("Не определена строка смены для сохранения материалов")

    drive = _get_drive()
    service = _get_service()

    saved_names: list[str] = []
    for index, entry in enumerate(photos, start=1):
        file_id = entry["file_id"]
        time_label = entry.get("time_label") or _format_time_label(dt.datetime.now())

        telegram_file = await message.bot.get_file(file_id)
        downloaded = await message.bot.download_file(telegram_file.file_path)
        content = _ensure_bytes(downloaded)

        ext = _normalize_extension(Path(telegram_file.file_path or "").suffix)
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
        raise RuntimeError("Не удалось сохранить фото материалов")

    public_url = await asyncio.to_thread(drive.folder_public_link, day_title)
    if not public_url:
        raise RuntimeError("Не удалось получить ссылку на папку материалов")

    await asyncio.to_thread(
        service.save_materials_block,
        row,
        pvd_m=data.get("pvd", 0),
        pvc_pcs=data.get("pvc", 0),
        tape_pcs=data.get("tape", 0),
        folder_link=public_url,
    )

    logger.info(
        "Материалы сохранены: user_id=%s, row=%s, файлов=%s", user_id, row, len(saved_names)
    )
    return public_url


@router.message(MaterialsState.CONFIRM)
async def handle_confirm(message: types.Message, state: FSMContext) -> None:
    """Финальное подтверждение данных или возврат к редактированию."""

    if await _handle_menu_button(message, state):
        return

    await _add_user_message(state, message.message_id, message.chat.id)
    text = (message.text or "").strip()
    if text == EDIT_BUTTON:
        context = await _get_context(state)
        context["tracker"] = _make_tracker()
        await _save_context(state, context)
        await _cleanup_step(message, state)
        await _ask_pvd(message, state)
        return
    if text != CONFIRM_BUTTON:
        reply = await message.answer("Используйте кнопки подтверждения на клавиатуре.")
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    await _cleanup_step(message, state)
    await flash_message(message, "💾 Сохраняю…", ttl=2.0)

    context = await _get_context(state)
    try:
        await _save_materials(context, message)
    except YaDiskError as exc:  # pragma: no cover - зависит от внешних сервисов
        logger.exception("Ошибка Яндекс.Диска при сохранении материалов: %s", exc)
        if exc.status in {401, 403}:
            reply = await message.answer(
                "Токен Яндекс.Диска недействителен или у приложения нет прав. Проверьте настройки и повторите попытку."
            )
        else:
            reply = await message.answer(
                "Не удалось загрузить материалы в хранилище. Попробуйте позже или обратитесь к администратору."
            )
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return
    except Exception as exc:  # pragma: no cover - защитный код
        logger.exception("Не удалось сохранить материалы: %s", exc)
        reply = await message.answer(
            "Не удалось сохранить материалы. Попробуйте позже или обратитесь к администратору."
        )
        await _add_bot_message(state, reply.message_id, message.chat.id)
        return

    from bot.handlers.shift_menu import mark_mode_done, render_shift_menu

    user_id = context.get("user_id")
    row = context.get("row")
    if not isinstance(user_id, int) or not isinstance(row, int):
        await message.answer("Не удалось определить строку смены для обновления меню.")
        await state.update_data(materials_ctx=None)
        return

    mark_mode_done(user_id, "materials")

    await cleanup_screen(message.bot, message.chat.id, keep_start=False)

    done_message = await message.answer(
        "Раздел «материалы» сохранён ✅",
        reply_markup=materials_remove_keyboard(),
    )

    await state.update_data(materials_ctx=None)
    await state.set_state(None)

    await render_shift_menu(
        message,
        user_id,
        row,
        state=state,
        service=_get_service(),
        delete_trigger_message=False,
        show_progress=False,
        use_screen_message=True,
    )

    if done_message:
        try:
            await message.bot.delete_message(message.chat.id, done_message.message_id)
        except TelegramBadRequest:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось удалить итоговое сообщение материалов")
