# TODO: добавить возможность выбирать время поминутно, а не только по часам
import asyncio
from typing import Any, Generator, TypeVar

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode
import aiohttp
import re
from sqlalchemy.orm import selectinload
from sqlalchemy import select
from static.models import Cooperator, Service, async_session, ReminderRecord
import datetime

API_TOKEN = '7670668813:AAG0jpvmYxuz5_K8h2H4fUh73ueojjMmIsI'
RUBITIME_API_KEY = '81ba535035724febc0d3c77183d6fc9dbdd259de0144ec96efe4710869d87710'
BRANCH_ID = 16725
SMSRU_API_ID = "3E27E597-8785-BB12-F52E-63C5247AC0FA"


def log_func_call(func_name: str, extra: str | None = None) -> None:
    """Логирует вызов функции."""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = f"[{now}] {func_name} called"
    if extra:
        msg += f" | {extra}"
    print(msg)


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


from aiogram.client.default import DefaultBotProperties
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
user_state: dict = {}


async def get_cooperators() -> list[Cooperator]:
    """Возвращает список сотрудников."""
    log_func_call("get_cooperators")
    async with async_session() as session:
        result = await session.execute(select(Cooperator).options(selectinload(Cooperator.services)))
        return result.scalars().all()


async def get_services_by_cooperator(cooperator_id: int) -> list[Service]:
    """Возвращает список услуг по сотруднику."""
    log_func_call("get_services_by_cooperator", f"cooperator_id={cooperator_id}")
    async with async_session() as session:
        result = await session.execute(select(Service).where(Service.cooperator_id == cooperator_id))
        return result.scalars().all()


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
        await bot.send_message("❌ Ошибка: не удалось связаться с сервером Rubitime. Попробуйте позже.")
        return None
    except asyncio.TimeoutError:
        await bot.send_message("❌ Ошибка: превышено время ожидания ответа от Rubitime.")
        return None
    except Exception as e:
        await bot.send_message(f"❌ Неизвестная ошибка: {e}")
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


@dp.message(F.text == "/start")
async def start(msg: Message) -> None:
    """Обработчик команды /start."""
    log_func_call("start", f"user_id={msg.from_user.id}")
    kb = get_lk_keyboard()
    await msg.answer(
        "👤 <b>Личный кабинет</b>:\n"
        "🗂 <b>Мои записи</b>\n"
        "📝 <b>Новая запись</b>\n"
        "❌ <b>Отмена записи</b>",
        reply_markup=kb
    )


@dp.message(F.text.in_(["/add", "📝 Новая запись"]))
async def add_record(msg: Message) -> None:
    """Начало сценария новой записи."""
    log_func_call("add_record", f"user_id={msg.from_user.id}")
    user_state[msg.from_user.id] = {"date_page": 0}
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
async def cancel_record(msg: Message) -> None:
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
        if uid not in user_state:
            user_state[uid] = {}
        user_state[uid]["cancel_list"] = [(r.id, r.rubitime_id, r.datetime) for r in recs]
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=f"{r.id}: {r.datetime.strftime('%Y-%m-%d %H:%M')}")] for r in recs],
            resize_keyboard=True
        )
        await msg.answer("❌ Выберите запись для отмены:", reply_markup=kb)


def is_lk_command(text: str) -> bool:
    """Проверяет, является ли текст командой личного кабинета."""
    return text in ["/my", "Мои записи", "/add", "Новая запись", "/cancel", "Отмена записи"]


@dp.message(F.func(lambda m: not is_lk_command(m.text) and "cooperator_id" not in user_state.get(m.from_user.id,
                                                                                                 {}) and not m.text.startswith(
    "/") and "cancel_list" not in user_state.get(m.from_user.id, {})))
async def select_cooperator(msg: Message) -> None:
    """Выбор сотрудника."""
    log_func_call("select_cooperator", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
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
    user_state[uid]["cooperator_id"] = cooperator_id
    services = await get_services_by_cooperator(cooperator_id)
    user_state[uid]["services"] = {s.id: s.name for s in services}
    names = [f"{s.id}: {s.name}" for s in services]
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=name)] for name in names],
        resize_keyboard=True
    )
    await msg.answer("💼 Выберите услугу:", reply_markup=kb)


@dp.message(F.func(lambda m: not is_lk_command(m.text) and "service_id" not in user_state.get(m.from_user.id,
                                                                                              {}) and "services" in user_state.get(
    m.from_user.id, {}) and not m.text.startswith("/") and "cancel_list" not in user_state.get(m.from_user.id, {})))
async def select_service(msg: Message) -> None:
    """Выбор услуги."""
    log_func_call("select_service", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    services = user_state[uid]["services"]
    text = msg.text.strip()
    try:
        service_id = int(text.split(":")[0])
    except Exception:
        await msg.answer("Пожалуйста, выберите услугу из списка.")
        return
    if service_id not in services:
        await msg.answer("Пожалуйста, выберите услугу из списка.")
        return
    user_state[uid]["service_id"] = service_id
    cooperator_id = user_state[uid]["cooperator_id"]
    schedule = await get_available_schedule(BRANCH_ID, cooperator_id, service_id)
    if not schedule:
        await msg.answer("Нет доступных дат для записи.")
        return
    user_state[uid]["schedule"] = schedule
    user_state[uid]["date_page"] = 0
    await send_date_page(msg, uid)


async def send_date_page(msg: Message, uid: int) -> None:
    """Показывает страницу выбора даты."""
    log_func_call("send_date_page", f"user_id={uid}")
    schedule = user_state[uid]["schedule"]
    dates = sorted(schedule.keys())
    page = user_state[uid].get("date_page", 0)
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


@dp.message(F.func(lambda m: not is_lk_command(m.text) and "date" not in user_state.get(m.from_user.id,
                                                                                        {}) and "schedule" in user_state.get(
    m.from_user.id, {}) and not m.text.startswith("/") and "cancel_list" not in user_state.get(m.from_user.id, {})))
async def select_date(msg: Message) -> None:
    """Выбор даты записи."""
    log_func_call("select_date", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    schedule = user_state[uid]["schedule"]
    dates = sorted(schedule.keys())
    page = user_state[uid].get("date_page", 0)
    pages = list(chunked(dates, 7))
    current = pages[page] if page < len(pages) else []
    text = msg.text.strip()
    if text == "Вперед >>":
        user_state[uid]["date_page"] = page + 1
        await send_date_page(msg, uid)
        return
    if text == "<< Назад":
        user_state[uid]["date_page"] = page - 1
        await send_date_page(msg, uid)
        return
    if text not in current:
        await msg.answer("Пожалуйста, выберите дату из списка.")
        return
    times = [t for t, v in schedule[text].items() if v["available"]]
    if not times:
        await msg.answer("Нет доступного времени на эту дату.")
        return
    user_state[uid]["date"] = text
    user_state[uid]["times"] = times
    await msg.answer(
        "⏰ Введите время в формате ЧЧ:ММ (например, 12:30).\nДоступно:\n" + ", ".join(times),
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(F.func(lambda m: not is_lk_command(m.text) and "datetime" not in user_state.get(m.from_user.id,
                                                                                            {}) and "times" in user_state.get(
    m.from_user.id, {}) and not m.text.startswith("/") and "cancel_list" not in user_state.get(m.from_user.id, {})))
async def select_time(msg: Message) -> None:
    """Выбор времени записи."""
    log_func_call("select_time", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    time = msg.text.strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", time):
        await msg.answer("Введите время в формате ЧЧ:ММ, например 12:30.")
        return
    available_times = user_state[uid]["times"]
    if time not in available_times:
        await msg.answer("Это время недоступно для записи. Доступные варианты:\n" + ", ".join(available_times))
        return
    service_id = user_state[uid]["service_id"]
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
    user_state[uid]["datetime"] = f"{user_state[uid]['date']} {time}:00"
    await msg.answer("👤 Введи имя:")


@dp.message(F.func(lambda m: not is_lk_command(m.text) and "name" not in user_state.get(m.from_user.id,
                                                                                        {}) and "datetime" in user_state.get(
    m.from_user.id, {}) and not m.text.startswith("/") and "cancel_list" not in user_state.get(m.from_user.id, {})))
async def get_name(msg: Message) -> None:
    """Получает имя пользователя для записи."""
    log_func_call("get_name", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    user_state[uid]["name"] = msg.text
    await msg.answer("📞 Введи номер телефона:")


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


@dp.message(F.func(lambda m: "cancel_list" in user_state.get(m.from_user.id, {}) and not m.text.startswith(
    "/") and "cancel_confirm" not in user_state.get(m.from_user.id, {})))
async def do_cancel(msg: Message) -> None:
    """Выбор записи для отмены."""
    log_func_call("do_cancel", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    cancel_list = user_state[uid]["cancel_list"]
    try:
        rec_id = int(msg.text.split(":")[0])
    except Exception:
        await msg.answer("Пожалуйста, выберите запись из списка.")
        return
    found = next((item for item in cancel_list if item[0] == rec_id), None)
    if not found:
        await msg.answer("Пожалуйста, выберите запись из списка.")
        return
    rubitime_id = found[1]
    dt = found[2]
    user_state[uid]["cancel_confirm"] = {
        "rec_id": rec_id,
        "rubitime_id": rubitime_id,
        "datetime": dt
    }
    await msg.answer(
        f"❓ Точно отменить запись на {dt.strftime('%Y-%m-%d %H:%M')}?",
        reply_markup=get_confirm_keyboard()
    )


@dp.message(F.func(lambda m: m.text == "Да" and "cancel_confirm" in user_state.get(m.from_user.id, {})))
async def confirm_cancel(msg: Message) -> None:
    """Подтверждение отмены записи."""
    log_func_call("confirm_cancel", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    confirm = user_state[uid]["cancel_confirm"]
    rec_id = confirm["rec_id"]
    rubitime_id = confirm["rubitime_id"]
    async with async_session() as session:
        rec = await session.get(ReminderRecord, rec_id)
        if not rec:
            await msg.answer("Запись не найдена.")
            user_state.pop(uid, None)
            return
        payload = {
            "id": rubitime_id,
            "rk": RUBITIME_API_KEY
        }
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post("https://rubitime.ru/api2/remove-record", json=payload,
                                             timeout=10) as resp:
                    res = await resp.json()
                    if res.get("status") == "ok":
                        await session.delete(rec)
                        await session.commit()
                        await msg.answer("✅ Запись отменена.", reply_markup=get_lk_keyboard())
                    else:
                        await msg.answer(f"Ошибка отмены: {res.get('message')}")
        except Exception as e:
            await msg.answer(f"Ошибка отмены: {e}")
    user_state.pop(uid, None)


@dp.message(F.func(lambda m: m.text == "Нет" and "cancel_confirm" in user_state.get(m.from_user.id, {})))
async def cancel_cancel(msg: Message) -> None:
    """Отмена отмены записи."""
    log_func_call("cancel_cancel", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    user_state.pop(uid, None)
    await msg.answer("❎ Отмена записи отменена.", reply_markup=get_lk_keyboard())


import random


def generate_sms_code() -> str:
    """Генерирует случайный SMS-код."""
    log_func_call("generate_sms_code")
    return str(random.randint(1000, 9999))


@dp.message(F.func(lambda m: not is_lk_command(m.text) and "phone" not in user_state.get(m.from_user.id,
                                                                                         {}) and "name" in user_state.get(
    m.from_user.id, {}) and not m.text.startswith("/") and "cancel_list" not in user_state.get(m.from_user.id, {})))
async def get_phone(msg: Message) -> None:
    """Получает номер телефона пользователя для записи."""
    log_func_call("get_phone", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    raw_phone = msg.text
    phone = normalize_phone(raw_phone)
    if not phone:
        await msg.answer("Введите номер телефона в формате +79000000000, 79000000000, 89000000000 или 9000000000.")
        return
    user_state[uid]["phone"] = phone
    async with async_session() as session:
        dt = datetime.datetime.strptime(user_state[uid]["datetime"], "%Y-%m-%d %H:%M:%S")
        exists = await session.execute(
            select(ReminderRecord).where(
                ReminderRecord.user_id == uid,
                ReminderRecord.datetime == dt
            )
        )
        if exists.scalars().first():
            await msg.answer("У вас уже есть запись на это время.")
            user_state.pop(uid, None)
            return
    sms_code = generate_sms_code()
    user_state[uid]["sms_code"] = sms_code
    sms_result = await send_sms_code(phone, sms_code)
    if sms_result.get("status") == "OK":
        sms_info = next(iter(sms_result["sms"].values()), {})
        print(f'SMS info: {sms_info}')
        if sms_info.get("status") == "OK":
            await msg.answer("Введите код из SMS для подтверждения записи:")
        else:
            await msg.answer("Ошибка отправки SMS. Попробуйте позже.")
            user_state.pop(uid, None)
            return
    else:
        await msg.answer("Ошибка отправки SMS. Попробуйте позже.")
        user_state.pop(uid, None)
        return


@dp.message(F.func(lambda m: "sms_code" in user_state.get(m.from_user.id, {})))
async def check_sms_code(msg: Message) -> None:
    """Проверяет введённый SMS-код."""
    log_func_call("check_sms_code", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    code = msg.text.strip()
    if code == user_state[uid]["sms_code"]:
        async with async_session() as db_session:
            cooperator = await db_session.get(Cooperator, user_state[uid]["cooperator_id"])
            service = await db_session.get(Service, user_state[uid]["service_id"])
        cooperator_name = cooperator.name if cooperator else "Неизвестно"
        service_name = service.name if service else "Неизвестно"
        user_state[uid]["confirm_data"] = {
            "cooperator_name": cooperator_name,
            "service_name": service_name,
            "datetime": user_state[uid]["datetime"],
            "phone": user_state[uid]["phone"],
            "name": user_state[uid]["name"]
        }
        confirm_text = (
            f"❓ <b>Точно хотите создать запись?</b>\n\n"
            f"🗓 <b>Дата:</b> {user_state[uid]['datetime']}\n"
            f"👨‍⚕️ <b>Врач:</b> {cooperator_name}\n"
            f"💼 <b>Услуга:</b> {service_name}\n"
            f"👤 <b>Имя:</b> {user_state[uid]['name']}\n"
            f"📞 <b>Телефон:</b> {user_state[uid]['phone']}"
        )
        await msg.answer(confirm_text, reply_markup=get_confirm_keyboard())
        user_state[uid].pop("sms_code", None)
    else:
        await msg.answer("Неверный код. Попробуйте ещё раз.")


@dp.message(F.func(lambda m: m.text == "Да" and "confirm_data" in user_state.get(m.from_user.id, {})))
async def confirm_create(msg: Message) -> None:
    """Подтверждает создание записи."""
    log_func_call("confirm_create", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    data = user_state[uid]
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
                        user_id=uid,
                        dt_str=data['datetime'],
                        name=data['name'],
                        phone=data['phone'],
                        rubitime_id=res["data"]["id"]
                    )
                else:
                    await msg.answer(f"❌ Ошибка: {res.get('message')}")
    except aiohttp.ClientError:
        await msg.answer("❌ Ошибка: не удалось связаться с сервером Rubitime. Попробуйте позже.")
    except asyncio.TimeoutError:
        await msg.answer("❌ Ошибка: превышено время ожидания ответа от Rubitime.")
    except Exception as e:
        await msg.answer(f"❌ Неизвестная ошибка: {e}")
    user_state.pop(uid, None)


@dp.message(F.func(lambda m: m.text == "Нет" and "confirm_data" in user_state.get(m.from_user.id, {})))
async def cancel_create(msg: Message) -> None:
    """Отменяет создание записи."""
    log_func_call("cancel_create", f"user_id={msg.from_user.id}")
    uid = msg.from_user.id
    user_state.pop(uid, None)
    await msg.answer("❎ Запись отменена.", reply_markup=get_lk_keyboard())


async def save_reminder_record(user_id: int, dt_str: str, name: str, phone: str, rubitime_id: int) -> None:
    """Сохраняет запись напоминания в базу."""
    log_func_call("save_reminder_record", f"user_id={user_id}, dt={dt_str}")
    dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    async with async_session() as session:
        record = ReminderRecord(
            user_id=user_id,
            datetime=dt,
            name=name,
            phone=phone,
            rubitime_id=rubitime_id
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
                select(ReminderRecord).where(ReminderRecord.datetime > now)
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
        async with async_session() as session:
            records = await session.execute(select(ReminderRecord))
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
                                await session.commit()
                                print(
                                    f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] sync: deleted local record id={rec.id} (rubitime_id={rec.rubitime_id})")
                except Exception as e:
                    print(
                        f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] sync: error for record id={rec.id}: {e}")
        await asyncio.sleep(60)


async def main() -> None:
    """Точка входа для запуска бота и фоновых задач."""
    log_func_call("main")
    reminder_task = asyncio.create_task(reminder_worker())
    sync_task = asyncio.create_task(sync_records_with_rubitime())
    await dp.start_polling(bot)
    reminder_task.cancel()
    sync_task.cancel()


if __name__ == "__main__":
    log_func_call("__main__")
    asyncio.run(main())
    print('Bot started')
