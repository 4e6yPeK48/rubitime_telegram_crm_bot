# TODO: добавить возможность выбирать время поминутно, а не только по часам
import asyncio
import datetime
import os
import random
import re
import time
from typing import Any, Generator, TypeVar

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import Message
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from static.models import Cooperator, Service, async_session, ReminderRecord

load_dotenv()

TELEGRAM_API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
RUBITIME_API_KEY = os.getenv("RUBITIME_API_KEY")
SMSRU_API_ID = os.getenv("SMSRU_API_ID")
BRANCH_ID = int(os.getenv("BRANCH_ID"))
CACHE_EXPIRED_TIMEOUT = int(os.getenv("CACHE_EXPIRED_TIMEOUT"))
PHONE_CONFIRMATION_ENABLED = os.getenv("PHONE_CONFIRMATION_ENABLED").lower() in ('true', '1', 't')


def log_func_call(func_name: str, extra: str | None = None) -> None:
    """Логирует вызов функции."""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = f"[{now}] {func_name} called"
    if extra:
        msg += f" | {extra}"
    print(msg)


def generate_sms_code() -> str:
    """Генерирует случайный SMS-код."""
    log_func_call("generate_sms_code")
    return str(random.randint(1000, 9999))


async def send_sms_code(phone: str, code: str) -> dict:
    """Отправляет SMS-код подтверждения."""
    log_func_call("send_sms_code", f"phone={phone}")
    url = "https://sms.ru/sms/send"
    phone = phone.lstrip("+")
    params = {
        "api_id": SMSRU_API_ID,
        "to": phone,
        "msg": f"Ваш код подтверждения: {code}",
        "json": 1
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"SMS.ru error: {resp.status}, {text}")
                return {"status": "ERROR", "message": text}
            return await resp.json()


def normalize_phone(phone: str) -> str | None:
    """Нормализует номер телефона."""
    phone = phone.strip().replace(' ', '').replace('-', '')
    if re.fullmatch(r"\+7\d{10}", phone):
        return phone
    if re.fullmatch(r"7\d{10}", phone):
        return f"+{phone}"
    if re.fullmatch(r"8\d{10}", phone):
        return f"+7{phone[1:]}"
    if re.fullmatch(r"\d{10}", phone):
        return f"+7{phone}"
    return None


from aiogram.client.default import DefaultBotProperties
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

bot = Bot(
    token=TELEGRAM_API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

_cooperators_cache = {"value": None, "ts": 0}
_services_cache = {}


def _cache_expired(ts, timeout=CACHE_EXPIRED_TIMEOUT):
    return time.time() - ts > timeout


async def get_cooperators(force_refresh=False) -> list[Cooperator]:
    """Возвращает список сотрудников с кэшированием."""
    log_func_call("get_cooperators")
    global _cooperators_cache
    if not force_refresh and _cooperators_cache["value"] is not None and not _cache_expired(_cooperators_cache["ts"]):
        return _cooperators_cache["value"]
    async with async_session() as session:
        result = await session.execute(select(Cooperator).options(selectinload(Cooperator.services)))
        cooperators = result.scalars().all()
        _cooperators_cache = {"value": cooperators, "ts": time.time()}
        return cooperators


async def get_services_by_cooperator(cooperator_id: int, force_refresh=False) -> list[Service]:
    """Возвращает список услуг по сотруднику с кэшированием."""
    log_func_call("get_services_by_cooperator", f"cooperator_id={cooperator_id}")
    global _services_cache
    cache = _services_cache.get(cooperator_id)
    if not force_refresh and cache and not _cache_expired(cache["ts"]):
        return cache["value"]
    async with async_session() as session:
        result = await session.execute(select(Service).where(Service.cooperator_id == cooperator_id))
        services = result.scalars().all()
        _services_cache[cooperator_id] = {"value": services, "ts": time.time()}
        return services


def clear_cooperators_cache():
    global _cooperators_cache
    _cooperators_cache = {"value": None, "ts": 0}


def clear_services_cache(cooperator_id=None):
    global _services_cache
    if cooperator_id is None:
        _services_cache = {}
    else:
        _services_cache.pop(cooperator_id, None)


async def get_available_schedule(branch_id: int, cooperator_id: int, service_id: int) -> dict | None:
    """Получает доступное расписание для записи."""
    log_func_call("get_available_schedule",
                  f"branch_id={branch_id}, cooperator_id={cooperator_id}, service_id={service_id}")
    payload = {
        "rk": RUBITIME_API_KEY,
        "branch_id": branch_id,
        "cooperator_id": cooperator_id,
        "service_id": service_id,
        "only_available": 1
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://rubitime.ru/api2/get-schedule", json=payload) as resp:
                res = await resp.json()
                if res.get("status") == "ok":
                    return res["data"]
                return {}
    except aiohttp.ClientError:
        return None
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


T = TypeVar("T")


def chunked(lst: list[T], n: int) -> Generator[list[T], Any, None]:
    """Разбивает список на чанки по n элементов."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def get_lk_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура личного кабинета."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗂 Мои записи")],
            [KeyboardButton(text="📝 Новая запись")],
            [KeyboardButton(text="❌ Отмена записи")],
        ],
        resize_keyboard=True
    )


def get_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура подтверждения."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Да"), KeyboardButton(text="Нет")]
        ],
        resize_keyboard=True
    )


from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    selecting_cooperator = State()
    selecting_service = State()
    selecting_date = State()
    selecting_time = State()
    entering_name = State()
    entering_phone = State()
    confirming_sms = State()
    confirming_create = State()
    cancelling_record = State()
    confirming_cancel = State()


@dp.message(F.text == "/start")
async def start(msg: Message, state: FSMContext) -> None:
    """Обработчик команды /start."""
    log_func_call("start", f"user_id={msg.from_user.id}")
    await state.clear()
    kb = get_lk_keyboard()
    await msg.answer(
        "👤 <b>Личный кабинет</b>:\n"
        "🗂 <b>Мои записи</b>\n"
        "📝 <b>Новая запись</b>\n"
        "❌ <b>Отмена записи</b>",
        reply_markup=kb
    )


@dp.message(F.text.in_(["/add", "📝 Новая запись"]))
async def add_record(msg: Message, state: FSMContext) -> None:
    """Начало сценария новой записи."""
    log_func_call("add_record", f"user_id={msg.from_user.id}")
    await state.clear()
    await state.set_state(BookingStates.selecting_cooperator)
    await state.update_data(date_page=0)
    cooperators = await get_cooperators()
    names = [f"{c.id}: {c.name}" for c in cooperators]
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=name)] for name in names],
        resize_keyboard=True
    )
    await msg.answer("👨‍⚕️ Выберите сотрудника:", reply_markup=kb)


@dp.message(F.text.in_(["/my", "🗂 Мои записи"]))
async def my_records(msg: Message) -> None:
    """Показывает записи пользователя."""
    log_func_call("my_records", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    async with async_session() as session:
        records = await session.execute(
            select(ReminderRecord).where(ReminderRecord.user_id == uid)
        )
        recs = records.scalars().all()
        if not recs:
            await msg.answer("ℹ️ У вас нет записей.")
            return
        text = "🗂 <b>Ваши записи</b>:\n"
        for r in recs:
            text += (
                f"🗓 <b>{r.datetime.strftime('%Y-%m-%d %H:%M')}</b>\n"
                f"👤 {r.name}\n"
                f"📞 {r.phone}\n"
                f"🆔 ID: {r.id}\n"
                "------\n"
            )
        await msg.answer(text)


@dp.message(F.text.in_(["/cancel", "❌ Отмена записи"]))
async def cancel_record(msg: Message, state: FSMContext) -> None:
    """Начало сценария отмены записи."""
    log_func_call("cancel_record", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    async with async_session() as session:
        records = await session.execute(
            select(ReminderRecord).where(ReminderRecord.user_id == uid)
        )
        recs = records.scalars().all()
        if not recs:
            await msg.answer("ℹ️ У вас нет записей для отмены.")
            return
        await state.set_state(BookingStates.cancelling_record)
        await state.update_data(cancel_list=[(r.id, r.rubitime_id, r.datetime) for r in recs])
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=f"{r.id}: {r.datetime.strftime('%Y-%m-%d %H:%M')}")] for r in recs],
            resize_keyboard=True
        )
        await msg.answer("❌ Выберите запись для отмены:", reply_markup=kb)


def is_lk_command(text: str) -> bool:
    """Проверяет, является ли текст командой личного кабинета."""
    return text in ["/my", "Мои записи", "/add", "Новая запись", "/cancel", "Отмена записи"]


@dp.message(F.func(lambda m: not is_lk_command(m.text)), BookingStates.selecting_cooperator)
async def select_cooperator(msg: Message, state: FSMContext) -> None:
    """Выбор сотрудника."""
    log_func_call("select_cooperator", f"user_id={msg.from_user.id}")
    text = msg.text.strip()
    try:
        cooperator_id = int(text.split(":")[0])
    except Exception:
        await msg.answer("Пожалуйста, выберите сотрудника из списка.")
        return
    cooperators = await get_cooperators()
    cooperator_ids = [c.id for c in cooperators]
    if cooperator_id not in cooperator_ids:
        await msg.answer("Пожалуйста, выберите сотрудника из списка.")
        return
    await state.update_data(cooperator_id=cooperator_id)
    services = await get_services_by_cooperator(cooperator_id)
    await state.update_data(services={s.id: s.name for s in services})
    names = [f"{s.id}: {s.name}" for s in services]
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=name)] for name in names],
        resize_keyboard=True
    )
    await state.set_state(BookingStates.selecting_service)
    await msg.answer("💼 Выберите услугу:", reply_markup=kb)


@dp.message(F.func(lambda m: not is_lk_command(m.text)), BookingStates.selecting_service)
async def select_service(msg: Message, state: FSMContext) -> None:
    """Выбор услуги."""
    log_func_call("select_service", f"user_id={msg.from_user.id}")
    data = await state.get_data()
    services = data.get("services", {})
    text = msg.text.strip()
    try:
        service_id = int(text.split(":")[0])
    except Exception:
        await msg.answer("Пожалуйста, выберите услугу из списка.")
        return
    if service_id not in services:
        await msg.answer("Пожалуйста, выберите услугу из списка.")
        return
    await state.update_data(service_id=service_id)
    cooperator_id = data["cooperator_id"]
    schedule = await get_available_schedule(BRANCH_ID, cooperator_id, service_id)
    if not schedule:
        await msg.answer("Нет доступных дат для записи.")
        return
    await state.update_data(schedule=schedule, date_page=0)
    await state.set_state(BookingStates.selecting_date)
    await send_date_page(msg, state)


async def send_date_page(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    schedule = data["schedule"]
    dates = sorted(schedule.keys())
    page = data.get("date_page", 0)
    pages = list(chunked(dates, 7))
    current = pages[page] if page < len(pages) else []
    kb_buttons = [[KeyboardButton(text=date)] for date in current]
    nav_buttons = []
    if page > 0:
        nav_buttons.append(KeyboardButton(text="<< Назад"))
    if page < len(pages) - 1:
        nav_buttons.append(KeyboardButton(text="Вперед >>"))
    if nav_buttons:
        kb_buttons.append(nav_buttons)
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True)
    await msg.answer("📅 Выберите дату:", reply_markup=kb)


@dp.message(F.func(lambda m: not is_lk_command(m.text)), BookingStates.selecting_date)
async def select_date(msg: Message, state: FSMContext) -> None:
    """Выбор даты записи."""
    log_func_call("select_date", f"user_id={msg.from_user.id}")
    data = await state.get_data()
    schedule = data["schedule"]
    dates = sorted(schedule.keys())
    page = data.get("date_page", 0)
    pages = list(chunked(dates, 7))
    current = pages[page] if page < len(pages) else []
    text = msg.text.strip()
    if text == "Вперед >>":
        await state.update_data(date_page=page + 1)
        await send_date_page(msg, state)
        return
    if text == "<< Назад":
        await state.update_data(date_page=page - 1)
        await send_date_page(msg, state)
        return
    if text not in current:
        await msg.answer("Пожалуйста, выберите дату из списка.")
        return
    times = [t for t, v in schedule[text].items() if v["available"]]
    if not times:
        await msg.answer("Нет доступного времени на эту дату.")
        return
    await state.update_data(date=text, times=times)
    await state.set_state(BookingStates.selecting_time)
    await msg.answer(
        "⏰ Введите время в формате ЧЧ:ММ (например, 12:30).\nДоступно:\n" + ", ".join(times),
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(F.func(lambda m: not is_lk_command(m.text)), BookingStates.selecting_time)
async def select_time(msg: Message, state: FSMContext) -> None:
    """Выбор времени записи."""
    log_func_call("select_time", f"user_id={msg.from_user.id}")
    data = await state.get_data()
    time = msg.text.strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", time):
        await msg.answer("Введите время в формате ЧЧ:ММ, например 12:30.")
        return
    available_times = data["times"]
    if time not in available_times:
        await msg.answer("Это время недоступно для записи. Доступные варианты:\n" + ", ".join(available_times))
        return
    service_id = data["service_id"]
    async with async_session() as session:
        service = await session.get(Service, service_id)
        duration = service.duration if service else 0
    start_hour, start_minute = map(int, time.split(":"))
    end_minute = start_minute + duration
    end_hour = start_hour + end_minute // 60
    end_minute = end_minute % 60
    if end_hour > 21 or (end_hour == 21 and end_minute > 0):
        await msg.answer("Услуга не успеет завершиться до конца рабочего дня (21:00).")
        return
    datetime_str = f"{data['date']} {time}:00"
    await state.update_data(datetime=datetime_str)
    await state.set_state(BookingStates.entering_name)
    await msg.answer("👤 Введи имя:")


@dp.message(F.func(lambda m: not is_lk_command(m.text)), BookingStates.entering_name)
async def get_name(msg: Message, state: FSMContext) -> None:
    """Получает имя пользователя для записи."""
    log_func_call("get_name", f"user_id={msg.from_user.id}")
    await state.update_data(name=msg.text)
    await state.set_state(BookingStates.entering_phone)
    await msg.answer("📞 Введи номер телефона:")


@dp.message(F.func(lambda m: not is_lk_command(m.text)), BookingStates.entering_phone)
async def get_phone(msg: Message, state: FSMContext) -> None:
    """Получает номер телефона пользователя для записи."""
    log_func_call("get_phone", f"user_id={msg.from_user.id}")
    data = await state.get_data()
    raw_phone = msg.text
    phone = normalize_phone(raw_phone)
    if not phone:
        await msg.answer("Введите номер телефона в формате +79000000000, 79000000000, 89000000000 или 9000000000.")
        return
    await state.update_data(phone=phone)
    async with async_session() as session:
        dt = datetime.datetime.strptime(data["datetime"], "%Y-%m-%d %H:%M:%S")
        exists = await session.execute(
            select(ReminderRecord).where(
                ReminderRecord.user_id == msg.from_user.id,
                ReminderRecord.datetime == dt
            )
        )
        if exists.scalars().first():
            await msg.answer("У вас уже есть запись на это время.")
            await state.clear()
            return
    if PHONE_CONFIRMATION_ENABLED:
        sms_code = generate_sms_code()
        await state.update_data(sms_code=sms_code)
        sms_result = await send_sms_code(phone, sms_code)
        if sms_result.get("status") == "OK":
            sms_info = next(iter(sms_result["sms"].values()), {})
            if sms_info.get("status") == "OK":
                await state.set_state(BookingStates.confirming_sms)
                await msg.answer("Введите код из SMS для подтверждения записи:")
            else:
                await msg.answer("Ошибка отправки SMS. Попробуйте позже.")
                await state.clear()
                return
        else:
            await msg.answer("Ошибка отправки SMS. Попробуйте позже.")
            await state.clear()
            return
    else:
        async with async_session() as db_session:
            cooperator = await db_session.get(Cooperator, data["cooperator_id"])
            service = await db_session.get(Service, data["service_id"])
        cooperator_name = cooperator.name if cooperator else "Неизвестно"
        service_name = service.name if service else "Неизвестно"
        confirm_data = {
            "cooperator_name": cooperator_name,
            "service_name": service_name,
            "datetime": data["datetime"],
            "phone": phone,
            "name": data["name"]
        }
        await state.update_data(confirm_data=confirm_data)
        confirm_text = (
            f"❓ <b>Точно хотите создать запись?</b>\n\n"
            f"🗓 <b>Дата:</b> {data['datetime']}\n"
            f"👨‍⚕️ <b>Врач:</b> {cooperator_name}\n"
            f"💼 <b>Услуга:</b> {service_name}\n"
            f"👤 <b>Имя:</b> {data['name']}\n"
            f"📞 <b>Телефон:</b> {phone}"
        )
        await state.set_state(BookingStates.confirming_create)
        await msg.answer(confirm_text, reply_markup=get_confirm_keyboard())


@dp.message(BookingStates.confirming_sms)
async def check_sms_code(msg: Message, state: FSMContext) -> None:
    log_func_call("check_sms_code", f"user_id={msg.from_user.id}")
    data = await state.get_data()
    code = msg.text.strip()
    if code == data["sms_code"]:
        async with async_session() as db_session:
            cooperator = await db_session.get(Cooperator, data["cooperator_id"])
            service = await db_session.get(Service, data["service_id"])
        cooperator_name = cooperator.name if cooperator else "Неизвестно"
        service_name = service.name if service else "Неизвестно"
        confirm_data = {
            "cooperator_name": cooperator_name,
            "service_name": service_name,
            "datetime": data["datetime"],
            "phone": data["phone"],
            "name": data["name"]
        }
        await state.update_data(confirm_data=confirm_data)
        confirm_text = (
            f"❓ <b>Точно хотите создать запись?</b>\n\n"
            f"🗓 <b>Дата:</b> {data['datetime']}\n"
            f"👨‍⚕️ <b>Врач:</b> {cooperator_name}\n"
            f"💼 <b>Услуга:</b> {service_name}\n"
            f"👤 <b>Имя:</b> {data['name']}\n"
            f"📞 <b>Телефон:</b> {data['phone']}"
        )
        await state.set_state(BookingStates.confirming_create)
        await msg.answer(confirm_text, reply_markup=get_confirm_keyboard())
        await state.update_data(sms_code=None)
    else:
        await msg.answer("Неверный код. Попробуйте ещё раз.")


@dp.message(F.text == "Да", BookingStates.confirming_create)
async def confirm_create(msg: Message, state: FSMContext) -> None:
    log_func_call("confirm_create", f"user_id={msg.from_user.id}")
    data = await state.get_data()
    confirm = data["confirm_data"]
    payload = {
        "rk": RUBITIME_API_KEY,
        "branch_id": BRANCH_ID,
        "cooperator_id": data["cooperator_id"],
        "service_id": data["service_id"],
        "status": 0,
        "record": data["datetime"],
        "name": data["name"],
        "phone": data["phone"]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://rubitime.ru/api2/create-record", json=payload, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "ok":
                    await msg.answer(
                        f"✅ <b>Запись создана!</b>\n"
                        f"🗓 <b>Дата:</b> {confirm['datetime']}\n"
                        f"👨‍⚕️ <b>Врач:</b> {confirm['cooperator_name']}\n"
                        f"💼 <b>Услуга:</b> {confirm['service_name']}\n"
                        f"👤 <b>Имя:</b> {confirm['name']}\n"
                        f"📞 <b>Телефон:</b> {confirm['phone']}\n"
                        f"🆔 <b>Rubitime Id:</b> {res['data']['id']}",
                        reply_markup=get_lk_keyboard()
                    )
                    await save_reminder_record(
                        user_id=msg.from_user.id,
                        dt_str=data['datetime'],
                        name=data['name'],
                        phone=data['phone'],
                        rubitime_id=res["data"]["id"],
                        confirmed=True
                    )
                else:
                    await msg.answer(f"❌ Ошибка: {res.get('message')}")
    except aiohttp.ClientError:
        await msg.answer("❌ Ошибка: не удалось связаться с сервером Rubitime. Попробуйте позже.")
    except asyncio.TimeoutError:
        await msg.answer("❌ Ошибка: превышено время ожидания ответа от Rubitime.")
    except Exception as e:
        await msg.answer(f"❌ Неизвестная ошибка: {e}")
    await state.clear()


@dp.message(F.text == "Нет", BookingStates.confirming_create)
async def cancel_create(msg: Message, state: FSMContext) -> None:
    log_func_call("cancel_create", f"user_id={msg.from_user.id}")
    await state.clear()
    await msg.answer("❎ Запись отменена.", reply_markup=get_lk_keyboard())


@dp.message(F.func(lambda m: not is_lk_command(m.text)), BookingStates.cancelling_record)
async def confirm_cancel_record(msg: Message, state: FSMContext) -> None:
    """Подтверждение отмены записи."""
    log_func_call("confirm_cancel_record", f"user_id={msg.from_user.id}")
    data = await state.get_data()
    cancel_list = data.get("cancel_list", [])
    text = msg.text.strip()
    try:
        record_id = int(text.split(":")[0])
    except ValueError:
        await msg.answer("Пожалуйста, выберите запись из списка.")
        return

    record = next((r for r in cancel_list if r[0] == record_id), None)
    if not record:
        await msg.answer("Пожалуйста, выберите запись из списка.")
        return

    await state.update_data(cancel_selected=record)
    await state.set_state(BookingStates.confirming_cancel)
    await msg.answer(
        f"❓ <b>Точно хотите отменить запись?</b>\n"
        f"🗓 <b>Дата:</b> {record[2].strftime('%Y-%m-%d %H:%M')}\n"
        f"🆔 <b>ID:</b> {record[0]}",
        reply_markup=get_confirm_keyboard()
    )


@dp.message(F.text == "Да", BookingStates.confirming_cancel)
async def do_cancel_record(msg: Message, state: FSMContext) -> None:
    """Выполняет отмену записи после подтверждения."""
    log_func_call("do_cancel_record", f"user_id={msg.from_user.id}")
    data = await state.get_data()
    record = data.get("cancel_selected")
    if not record:
        await msg.answer("Ошибка: запись не выбрана.")
        await state.clear()
        return

    record_id, rubitime_id, dt = record
    payload = {
        "rk": RUBITIME_API_KEY,
        "id": rubitime_id
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://rubitime.ru/api2/remove-record", json=payload, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "ok":
                    async with async_session() as db_session:
                        rec = await db_session.get(ReminderRecord, record_id)
                        if rec:
                            await db_session.delete(rec)
                            await db_session.commit()
                    await msg.answer("✅ Запись успешно отменена.", reply_markup=get_lk_keyboard())
                else:
                    await msg.answer(f"❌ Ошибка отмены записи: {res.get('message')}")
    except aiohttp.ClientError:
        await msg.answer("❌ Ошибка: не удалось связаться с сервером Rubitime. Попробуйте позже.")
    except asyncio.TimeoutError:
        await msg.answer("❌ Ошибка: превышено время ожидания ответа от Rubitime.")
    except Exception as e:
        await msg.answer(f"❌ Неизвестная ошибка: {e}")
    await state.clear()


@dp.message(F.text == "Нет", BookingStates.confirming_cancel)
async def cancel_cancel_record(msg: Message, state: FSMContext) -> None:
    """Отмена отмены записи."""
    log_func_call("cancel_cancel_record", f"user_id={msg.from_user.id}")
    await state.clear()
    await msg.answer("❎ Отмена отмены записи.", reply_markup=get_lk_keyboard())


async def save_reminder_record(user_id: int, dt_str: str, name: str, phone: str, rubitime_id: int,
                               confirmed: bool = False) -> None:
    """Сохраняет запись напоминания в базу."""
    log_func_call("save_reminder_record", f"user_id={user_id}, dt={dt_str}")
    dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    async with async_session() as session:
        record = ReminderRecord(
            user_id=user_id,
            datetime=dt,
            name=name,
            phone=phone,
            rubitime_id=rubitime_id,
            confirmed=confirmed
        )
        session.add(record)
        await session.commit()


async def reminder_worker() -> None:
    """Фоновая задача для отправки напоминаний."""
    log_func_call("reminder_worker")
    while True:
        now = datetime.datetime.now()
        async with async_session() as session:
            records = await session.execute(
                select(ReminderRecord)
                .where(ReminderRecord.datetime > now)
                .where(
                    (
                            (ReminderRecord.reminded_24h == False) &
                            (ReminderRecord.datetime <= now + datetime.timedelta(hours=24)) &
                            (ReminderRecord.datetime > now + datetime.timedelta(hours=12))
                    )
                    |
                    (
                            (ReminderRecord.reminded_12h == False) &
                            (ReminderRecord.datetime <= now + datetime.timedelta(hours=12)) &
                            (ReminderRecord.datetime > now)
                    )
                )
            )
            for rec in records.scalars():
                delta = rec.datetime - now
                if 24 * 3600 >= delta.total_seconds() > 12 * 3600 and not rec.reminded_24h:
                    try:
                        await bot.send_message(
                            rec.user_id,
                            f"⏰ Напоминание: ваша запись на {rec.datetime.strftime('%Y-%m-%d %H:%M')} через 24 часа."
                        )
                        rec.reminded_24h = True
                    except Exception:
                        pass
                if 12 * 3600 >= delta.total_seconds() > 0 and not rec.reminded_12h:
                    try:
                        await bot.send_message(
                            rec.user_id,
                            f"⏰ Напоминание: ваша запись на {rec.datetime.strftime('%Y-%m-%d %H:%M')} через 12 часов."
                        )
                        rec.reminded_12h = True
                    except Exception:
                        pass
            await session.commit()
        await asyncio.sleep(600)


async def sync_records_with_rubitime() -> None:
    """Фоновая задача для синхронизации записей с Rubitime."""
    log_func_call("sync_records_with_rubitime")
    while True:
        now = datetime.datetime.now()
        threshold_time = now - datetime.timedelta(minutes=5)
        async with async_session() as session:
            records = await session.execute(
                select(ReminderRecord).where(
                    ReminderRecord.datetime > now,
                    ReminderRecord.confirmed == True,
                    ReminderRecord.datetime <= threshold_time
                )
            )
            recs = records.scalars().all()
            for rec in recs:
                payload = {
                    "id": rec.rubitime_id,
                    "rk": RUBITIME_API_KEY
                }
                try:
                    async with aiohttp.ClientSession() as http_session:
                        async with http_session.post("https://rubitime.ru/api2/get-record", json=payload,
                                                     timeout=10) as resp:
                            res = await resp.json()
                            if res.get("status") == "error":
                                await session.delete(rec)
                                print(
                                    f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] sync: deleted local record id={rec.id} (rubitime_id={rec.rubitime_id})"
                                )
                except Exception as e:
                    print(
                        f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] sync: error for record id={rec.id}: {e}"
                    )
            await session.commit()
        await asyncio.sleep(60)


async def main() -> None:
    """Точка входа для запуска бота и фоновых задач."""
    log_func_call("main")
    reminder_task = asyncio.create_task(reminder_worker())
    sync_task = asyncio.create_task(sync_records_with_rubitime())
    await dp.start_polling(bot)


if __name__ == "__main__":
    log_func_call("__main__")
    asyncio.run(main())
