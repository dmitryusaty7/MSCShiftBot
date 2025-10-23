from __future__ import annotations

from typing import Dict, List, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from .dependencies import sheets_service
from .main import (ActiveShift, MainStates, build_keyboard, go_to_shift_menu,
                   shift_menu_keyboard)

router = Router()


class MaterialsStates(StatesGroup):
    """Состояния раздела материалов."""

    pvd_in = State()
    pvc_in = State()
    tape_in = State()
    pvd_out = State()
    pvc_out = State()
    tape_out = State()
    photos = State()
    review = State()


MATERIALS_ORDER = [
    "pvd_in",
    "pvc_in",
    "tape_in",
    "pvd_out",
    "pvc_out",
    "tape_out",
    "photos",
]

MATERIALS_LABELS = {
    "pvd_in": "Рулоны ПВД — поступление",
    "pvc_in": "Трубки ПВХ — поступление",
    "tape_in": "Клейкая лента — поступление",
    "pvd_out": "Рулоны ПВД — расход",
    "pvc_out": "Трубки ПВХ — расход",
    "tape_out": "Клейкая лента — расход",
    "photos": "Фото крепления",
}

MATERIALS_MESSAGES = {
    "pvd_in": "Укажите количество поступивших рулонов ПВД (в метрах).",
    "pvc_in": "Укажите количество поступивших трубок ПВХ (в штуках).",
    "tape_in": "Укажите количество поступившей клейкой ленты (в штуках).",
    "pvd_out": "Укажите расход рулонов ПВД (в метрах).",
    "pvc_out": "Укажите расход трубок ПВХ (в штуках).",
    "tape_out": "Укажите расход клейкой ленты (в штуках).",
}

MATERIALS_STATE_BY_KEY = {
    "pvd_in": MaterialsStates.pvd_in,
    "pvc_in": MaterialsStates.pvc_in,
    "tape_in": MaterialsStates.tape_in,
    "pvd_out": MaterialsStates.pvd_out,
    "pvc_out": MaterialsStates.pvc_out,
    "tape_out": MaterialsStates.tape_out,
    "photos": MaterialsStates.photos,
}


def materials_photo_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Подтвердить")],
            [KeyboardButton(text="Изменить")],
            [KeyboardButton(text="⬅ Назад"), KeyboardButton(text="В меню")],
        ],
        resize_keyboard=True,
    )


async def ensure_materials_context(state: FSMContext, materials_row: int) -> Dict[str, str]:
    data = await state.get_data()
    materials_data: Optional[Dict[str, str]] = data.get("materials_data")
    if materials_data is None:
        materials_data = await sheets_service.get_materials_details(materials_row)
        await state.update_data(materials_data=materials_data)
    if data.get("materials_photos") is None:
        await state.update_data(materials_photos=[])
    return materials_data


async def prompt_materials_numeric(message: Message, state: FSMContext, key: str) -> None:
    data = await state.get_data()
    materials = data.get("materials_data", {})
    current = materials.get(key, "")
    suffix = f"\nТекущие данные: {current}" if current else ""
    await state.set_state(MATERIALS_STATE_BY_KEY[key])
    await message.answer(
        MATERIALS_MESSAGES[key] + suffix,
        reply_markup=build_keyboard(include_skip=True),
    )


async def prompt_materials_photos(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    materials = data.get("materials_data", {})
    link = materials.get("photo_link", "")
    pending = data.get("materials_photos") or []
    note_parts = []
    if link:
        note_parts.append(f"Текущая ссылка: {link}")
    if pending:
        note_parts.append(f"Получено новых файлов: {len(pending)}")
    note = f"\n{' '.join(note_parts)}" if note_parts else ""
    await state.set_state(MaterialsStates.photos)
    await message.answer(
        "Прикрепите фото крепления. Можно загрузить несколько файлов подряд. После завершения нажмите 'Подтвердить'."
        + note,
        reply_markup=materials_photo_keyboard(),
    )


async def prompt_materials_step(message: Message, state: FSMContext, key: str) -> None:
    if key == "photos":
        await prompt_materials_photos(message, state)
    else:
        await prompt_materials_numeric(message, state, key)


async def go_to_previous_materials_step(message: Message, state: FSMContext, current_key: str) -> None:
    index = MATERIALS_ORDER.index(current_key)
    if index == 0:
        await go_to_shift_menu(message, state)
        return
    previous_key = MATERIALS_ORDER[index - 1]
    await prompt_materials_step(message, state, previous_key)


async def go_to_next_materials_step(message: Message, state: FSMContext, current_key: str) -> None:
    index = MATERIALS_ORDER.index(current_key)
    if index == len(MATERIALS_ORDER) - 1:
        await show_materials_review(message, state)
        return
    next_key = MATERIALS_ORDER[index + 1]
    await prompt_materials_step(message, state, next_key)


async def show_materials_review(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    materials: Dict[str, str] = data.get("materials_data", {})
    lines = []
    for key in MATERIALS_ORDER[:-1]:
        value = materials.get(key, "") or "0"
        lines.append(f"{MATERIALS_LABELS[key]}: {value}")
    link = materials.get("photo_link") or "—"
    lines.append(f"{MATERIALS_LABELS['photos']}: {link}")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data="materials:confirm")],
            [InlineKeyboardButton(text="Изменить", callback_data="materials:edit")],
        ]
    )
    await state.set_state(MaterialsStates.review)
    await message.answer(
        "Проверьте введённые данные по материалам. Всё верно?\n\n" + "\n".join(lines),
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "shift_section:materials", MainStates.shift_menu)
async def start_materials(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена не найдена", show_alert=True)
        return
    materials_row = active_shift.rows.get("materials")
    if not materials_row:
        await callback.answer("Строка материалов не найдена", show_alert=True)
        return
    await ensure_materials_context(state, materials_row)
    await state.update_data(materials_photos=[])
    await prompt_materials_step(callback.message, state, MATERIALS_ORDER[0])
    await callback.answer()


async def handle_materials_numeric_input(message: Message, state: FSMContext, key: str) -> None:
    text = (message.text or "").strip()
    lower = text.lower()
    if lower == "в меню":
        await go_to_shift_menu(message, state)
        return
    if lower == "⬅ назад":
        await go_to_previous_materials_step(message, state, key)
        return
    if lower == "пропустить":
        value = "0"
    else:
        cleaned = text.replace(" ", "")
        if not cleaned.isdigit():
            await message.answer("Пожалуйста, укажите целое число или нажмите 'Пропустить'.")
            return
        value = str(int(cleaned))

    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await message.answer("Активная смена не найдена.")
        return
    materials_row = active_shift.rows.get("materials")
    if not materials_row:
        await message.answer("Строка материалов не найдена.")
        return
    materials = await ensure_materials_context(state, materials_row)
    materials[key] = value
    await state.update_data(materials_data=materials)
    await go_to_next_materials_step(message, state, key)


@router.message(MaterialsStates.pvd_in)
async def materials_pvd_in_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "pvd_in")


@router.message(MaterialsStates.pvc_in)
async def materials_pvc_in_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "pvc_in")


@router.message(MaterialsStates.tape_in)
async def materials_tape_in_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "tape_in")


@router.message(MaterialsStates.pvd_out)
async def materials_pvd_out_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "pvd_out")


@router.message(MaterialsStates.pvc_out)
async def materials_pvc_out_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "pvc_out")


@router.message(MaterialsStates.tape_out)
async def materials_tape_out_input(message: Message, state: FSMContext) -> None:
    await handle_materials_numeric_input(message, state, "tape_out")


@router.message(MaterialsStates.photos)
async def materials_photos_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = (message.text or "").strip()
    lower = text.lower()
    if message.photo:
        file_id = message.photo[-1].file_id
        pending: List[str] = data.get("materials_photos") or []
        pending.append(file_id)
        await state.update_data(materials_photos=pending)
        await message.answer(
            f"Фото получено. Всего новых файлов: {len(pending)}.",
            reply_markup=materials_photo_keyboard(),
        )
        return
    if lower == "в меню":
        await go_to_shift_menu(message, state)
        return
    if lower == "⬅ назад":
        await go_to_previous_materials_step(message, state, "photos")
        return
    if lower == "изменить":
        materials = data.get("materials_data") or {}
        materials["photo_link"] = ""
        await state.update_data(materials_data=materials, materials_photos=[])
        await message.answer(
            "Старые файлы очищены. Отправьте новые фотографии.",
            reply_markup=materials_photo_keyboard(),
        )
        return
    if lower == "подтвердить":
        active_shift: Optional[ActiveShift] = data.get("active_shift")
        if not active_shift:
            await message.answer("Активная смена не найдена.")
            return
        materials_row = active_shift.rows.get("materials")
        if not materials_row:
            await message.answer("Строка материалов не найдена.")
            return
        materials = await ensure_materials_context(state, materials_row)
        pending: List[str] = data.get("materials_photos") or []
        link = materials.get("photo_link", "")
        if pending:
            link = await sheets_service.register_materials_photos(materials_row, pending)
            await state.update_data(materials_photos=[])
        materials["photo_link"] = link
        await state.update_data(materials_data=materials)
        await message.answer(
            "Фото обработаны.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await go_to_next_materials_step(message, state, "photos")
        return
    await message.answer(
        "Отправьте фотографии или используйте кнопки управления.",
        reply_markup=materials_photo_keyboard(),
    )


@router.callback_query(F.data == "materials:confirm", MaterialsStates.review)
async def materials_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена не найдена", show_alert=True)
        return
    materials_row = active_shift.rows.get("materials")
    if not materials_row:
        await callback.answer("Строка материалов не найдена", show_alert=True)
        return
    materials: Dict[str, str] = data.get("materials_data") or {}
    pending: List[str] = data.get("materials_photos") or []
    link = materials.get("photo_link", "")
    if pending:
        link = await sheets_service.register_materials_photos(materials_row, pending)
        await state.update_data(materials_photos=[])
        materials["photo_link"] = link
    normalized: Dict[str, str] = {}
    for key in MATERIALS_ORDER[:-1]:
        raw = str(materials.get(key, "") or "0")
        cleaned = raw.replace(" ", "")
        normalized[key] = str(int(cleaned)) if cleaned.isdigit() else "0"
    await sheets_service.save_materials_numbers(materials_row, normalized)
    await sheets_service.save_materials_photo_link(materials_row, link)
    materials.update(normalized)
    materials["photo_link"] = link
    await state.update_data(materials_data=materials, materials_photos=[])
    active_shift.sections["materials"] = True
    await state.update_data(active_shift=active_shift)
    await state.set_state(MainStates.shift_menu)
    await callback.message.answer(
        "Раздел «Материалы» сохранён.",
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )
    await callback.answer("Раздел заполнен")


@router.callback_query(F.data == "materials:edit", MaterialsStates.review)
async def materials_edit(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Активная смена не найдена", show_alert=True)
        return
    materials_row = active_shift.rows.get("materials")
    if not materials_row:
        await callback.answer("Строка материалов не найдена", show_alert=True)
        return
    await ensure_materials_context(state, materials_row)
    await state.update_data(materials_photos=[])
    await prompt_materials_step(callback.message, state, MATERIALS_ORDER[0])
    await callback.answer()

