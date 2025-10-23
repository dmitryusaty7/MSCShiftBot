from __future__ import annotations

from typing import List, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from .dependencies import sheets_service
from .main import ActiveShift, MainStates, shift_menu_keyboard

router = Router()


class TeamStates(StatesGroup):
    """Состояния выбора состава бригады."""

    select_driver = State()
    select_workers = State()
    confirm = State()


def crew_driver_keyboard(drivers: List[str]) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=name)] for name in drivers[:20]]
    keyboard.append([KeyboardButton(text="Отмена")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def crew_workers_keyboard(workers: List[str]) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=name)] for name in workers[:20]]
    keyboard.append([KeyboardButton(text="Готово")])
    keyboard.append([KeyboardButton(text="Отмена")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


@router.callback_query(F.data == "shift_section:crew", MainStates.shift_menu)
async def start_crew(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Смена не найдена", show_alert=True)
        return
    drivers = await sheets_service.get_drivers_directory()
    if not drivers:
        await callback.answer("Нет данных о водителях", show_alert=True)
        return
    await state.set_state(TeamStates.select_driver)
    await callback.message.answer(
        "Выберите водителя:", reply_markup=crew_driver_keyboard(drivers)
    )
    await callback.answer()


@router.message(TeamStates.select_driver)
async def crew_select_driver(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    lower_text = text.lower()
    if lower_text == "отмена":
        await state.set_state(MainStates.shift_menu)
        data = await state.get_data()
        active_shift: Optional[ActiveShift] = data.get("active_shift")
        if active_shift:
            await message.answer(
                "Возвращаю в меню смены.",
                reply_markup=shift_menu_keyboard(active_shift.sections),
            )
        else:
            await message.answer("Возврат в меню.", reply_markup=ReplyKeyboardRemove())
        return

    drivers = await sheets_service.get_drivers_directory()
    if text not in drivers:
        await message.answer("Пожалуйста, выберите водителя из списка.")
        return

    await state.update_data(selected_driver=text, selected_workers=[])
    await state.set_state(TeamStates.select_workers)
    workers = await sheets_service.get_workers_directory()
    if not workers:
        await state.set_state(MainStates.shift_menu)
        data = await state.get_data()
        active_shift: Optional[ActiveShift] = data.get("active_shift")
        if active_shift:
            await message.answer(
                "Справочник рабочих пуст. Обратитесь к администратору.",
                reply_markup=shift_menu_keyboard(active_shift.sections),
            )
        return
    await message.answer(
        "Теперь выберите рабочих. Можно выбрать несколько имён подряд, после чего нажмите «Готово».",
        reply_markup=crew_workers_keyboard(workers),
    )


@router.message(TeamStates.select_workers)
async def crew_select_workers(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    lower_text = text.lower()
    if lower_text == "отмена":
        await state.set_state(MainStates.shift_menu)
        data = await state.get_data()
        active_shift: Optional[ActiveShift] = data.get("active_shift")
        if active_shift:
            await message.answer(
                "Возвращаю в меню смены.",
                reply_markup=shift_menu_keyboard(active_shift.sections),
            )
        return

    workers_directory = await sheets_service.get_workers_directory()
    data = await state.get_data()
    selected_workers: List[str] = data.get("selected_workers", [])

    if lower_text == "готово":
        if not selected_workers:
            await message.answer("Выберите хотя бы одного рабочего.")
            return
        await state.set_state(TeamStates.confirm)
        driver = data.get("selected_driver")
        summary = "\n".join([f"• {name}" for name in selected_workers])
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Сохранить", callback_data="crew:save")],
                [InlineKeyboardButton(text="Изменить", callback_data="crew:restart")],
            ]
        )
        await message.answer(
            f"Проверьте состав:\nВодитель: {driver}\nРабочие:\n{summary}",
            reply_markup=keyboard,
        )
        return

    if text not in workers_directory:
        await message.answer("Выбирайте рабочих из списка или нажмите «Готово».")
        return

    if text in selected_workers:
        await message.answer("Этот рабочий уже добавлен.")
        return

    selected_workers.append(text)
    await state.update_data(selected_workers=selected_workers)
    await message.answer("Добавлено. Продолжайте выбор или нажмите «Готово».")


@router.callback_query(F.data == "crew:restart", TeamStates.confirm)
async def crew_restart(callback: CallbackQuery, state: FSMContext) -> None:
    drivers = await sheets_service.get_drivers_directory()
    if not drivers:
        await callback.answer("Нет данных о водителях", show_alert=True)
        return
    await state.set_state(TeamStates.select_driver)
    await state.update_data(selected_workers=[], selected_driver="")
    await callback.message.answer(
        "Выберите водителя:", reply_markup=crew_driver_keyboard(drivers)
    )
    await callback.answer()


@router.callback_query(F.data == "crew:save", TeamStates.confirm)
async def crew_save(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    active_shift: Optional[ActiveShift] = data.get("active_shift")
    if not active_shift:
        await callback.answer("Нет активной смены", show_alert=True)
        return
    driver = data.get("selected_driver", "")
    workers = data.get("selected_workers", [])
    await sheets_service.save_crew(active_shift.rows["crew"], driver, workers)
    active_shift.sections["crew"] = True
    await state.update_data(active_shift=active_shift)
    await state.set_state(MainStates.shift_menu)
    await callback.message.answer(
        "Состав бригады сохранён.",
        reply_markup=shift_menu_keyboard(active_shift.sections),
    )
    await callback.answer()

