import asyncio
import json
import logging
import random
import os
from datetime import datetime, date

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


# ================= НАСТРОЙКИ =================
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    logging.error("Не задан TOKEN! Запусти с переменной окружения TOKEN=...")

logging.basicConfig(level=logging.INFO)
DATA_FILE = "bot_data.json"


def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

data = load_data()
reminders = []          # напоминания (живут до перезапуска бота)
chat_history = {}       # uid -> последние сообщения пользователя


# ================= СОСТОЯНИЯ =================
class DialogStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_bday = State()
    waiting_for_note = State()
    waiting_for_guess = State()
    waiting_for_reminder = State()


# ================= МЕНЮ =================
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌤 Погода"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🎂 День рождения")],
        [KeyboardButton(text="📝 Заметки и память"), KeyboardButton(text="💬 Поболтать")],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выбери категорию 👇",
    is_persistent=True
)

WEATHER_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌤 Сейчас"), KeyboardButton(text="🌙 Завтра")],
        [KeyboardButton(text="📍 Указать город"), KeyboardButton(text="🏠 Главное меню")],
    ],
    resize_keyboard=True
)

GAMES_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎲 Угадай число"), KeyboardButton(text="🎯 Случайный факт")],
        [KeyboardButton(text="🏠 Главное меню")],
    ],
    resize_keyboard=True
)

BDAY_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Установить ДР"), KeyboardButton(text="🎂 Дней до ДР")],
        [KeyboardButton(text="🏠 Главное меню")],
    ],
    resize_keyboard=True
)

NOTES_MEMORY_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Мои заметки"), KeyboardButton(text="🧠 Запомнить что‑то")],
        [KeyboardButton(text="⏰ Напомнить"), KeyboardButton(text="🗑️ Очистить заметки")],
        [KeyboardButton(text="🏠 Главное меню")],
    ],
    resize_keyboard=True
)

HELP_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="❓ Как пользоваться"), KeyboardButton(text="📚 Команды")],
        [KeyboardButton(text="🏠 Главное меню")],
    ],
    resize_keyboard=True
)


# ================= ВСПОМОГАТЕЛЬНОЕ =================
def get_user_data(uid):
    uid = str(uid)
    data.setdefault("users", {})
    return data["users"].setdefault(uid, {})

def get_time_of_day():
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 18:
        return "Добрый день"
    return "Добрый вечер"

def weather_text(code):
    codes = {
        0: "ясно ☀️", 1: "преимущественно ясно 🌤", 2: "переменная облачность ⛅",
        3: "пасмурно ☁️", 45: "туман 🌫", 48: "изморозь 🌫",
        51: "небольшая морось 🌦", 53: "морось 🌦", 55: "сильная морось 🌧",
        61: "небольшой дождь 🌦", 63: "дождь 🌧", 65: "сильный дождь 🌧",
        71: "небольшой снег 🌨", 73: "снег 🌨", 75: "сильный снег ❄️",
        80: "ливень 🌦", 81: "ливень 🌧", 82: "сильный ливень ⛈",
        95: "гроза ⛈", 96: "гроза с градом ⛈", 99: "сильная гроза с градом ⛈",
    }
    return codes.get(code, "погода обычная 🙂")

async def geocode_city(city):
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city, "count": 1, "language": "ru", "format": "json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                result = await resp.json()
    except Exception:
        return None
    results = result.get("results")
    if not results:
        return None
    first = results[0]
    return first["latitude"], first["longitude"], first["name"]

async def fetch_weather(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "timezone": "auto",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                return await resp.json()
    except Exception:
        return None

FACTS = [
    "У осьминога три сердца: одно качает кровь по телу, а два других — к жабрам.",
    "Луна удаляется от Земли примерно на 3,8 см в год.",
    "В человеческом мозге около 86 миллиардов нейронов.",
    "Пчёлы умеют танцевать, чтобы показать другим, где находится нектар.",
    "Самая высокая гора в Солнечной системе — вулкан Олимп на Марсе.",
    "У кошек есть «второй нос» — орган Якобсона.",
    "На Венере день длиннее года.",
    "Медузы могут быть бессмертными: вид Turritopsis dohrnii.",
    "Слоны узнают себя в зеркале.",
    "Молния горячее поверхности Солнца: до 30 000 °C.",
]

TOPIC_REPLIES = {
    ("кот", "кошка", "котёнок", "котенок"): [
        "Обожаю котиков! Расскажи про своего? 😸",
        "Котики — бесконечная тема. Как зовут твоего?",
    ],
    ("собака", "пёс", "пес", "щенок"): [
        "Собаки — про преданность. Какая порода тебе ближе? 🐶",
    ],
    ("космос", "планета", "звезда", "луна"): [
        "Космос — вечная загадка. Луна каждый год отдаляется от Земли на пару сантиметров.",
    ],
    ("фильм", "кино", "сериал", "мультфильм"): [
        "Кино — отдельный мир! Что смотришь сейчас? Без спойлеров 😄",
    ],
    ("привет", "здравствуй", "хай", "ку"): [
        "Привет-привет! Как настроение? Что рассказать?",
        "О, привет! Начинай 🙂",
    ],
    ("скучно", "чем заняться", "не знаю что делать"): [
        "Скучно — значит, пора на приключения! Сыграем в «Угадай число» или расскажу факт?",
    ],
}

UNIVERSAL_REPLIES = [
    "Интересно! Расскажи чуть подробнее 🙂",
    "Ого. А что ты об этом думаешь?",
    "Понимаю. И как ты к этому относишься?",
]

def get_topic_reply(text):
    lower = text.lower()
    for words, replies in TOPIC_REPLIES.items():
        if any(w in lower for w in words):
            return random.choice(replies)
    return random.choice(UNIVERSAL_REPLIES)


# ================= ДЕНЬ РОЖДЕНИЯ =================
def calculate_days_until_bday(bday_str):
    today = date.today()
    bday = datetime.strptime(bday_str, "%d.%m").date()
    next_bday = bday.replace(year=today.year)
    if next_bday < today:
        next_bday = next_bday.replace(year=today.year + 1)
    return (next_bday - today).days

@dp.message(F.text == "🎂 День рождения")
async def bday_category(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=BDAY_MENU)

@dp.message(F.text == "📅 Установить ДР")
async def set_bday_start(message: types.Message, state: FSMContext):
    await message.answer("Введи дату ДР в формате ДД.ММ (например, 15.05):")
    await state.set_state(DialogStates.waiting_for_bday)

@dp.message(DialogStates.waiting_for_bday)
async def set_bday_finish(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    user = get_user_data(user_id)
    bday_input = message.text.strip()
    try:
        datetime.strptime(bday_input, "%d.%m")
        user["bday"] = bday_input
        save_data(data)
        await message.answer(f"✅ День рождения установлен: {bday_input}")
    except ValueError:
        await message.answer("Неверный формат. Введи ДД.ММ, например 15.05")
    await state.clear()

@dp.message(F.text == "🎂 Дней до ДР")
async def show_bday_count(message: types.Message):
    user_id = str(message.from_user.id)
    user = get_user_data(user_id)
    bday = user.get("bday")
    if not bday:
        await message.answer("Сначала установи день рождения кнопкой «📅 Установить ДР».")
        return
    days = calculate_days_until_bday(bday)
    await message.answer(f"🎂 До твоего дня рождения осталось {days} дней!")


# ================= ЗАМЕТКИ И ПАМЯТЬ =================
@dp.message(F.text == "📝 Заметки и память")
async def notes_memory_category(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=NOTES_MEMORY_MENU)

@dp.message(F.text == "📝 Мои заметки")
async def show_notes(message: types.Message):
    user_id = str(message.from_user.id)
    user = get_user_data(user_id)
    notes = user.get("notes", [])
    if not notes:
        await message.answer("У тебя пока нет сохранённых заметок.")
        return
    notes_text = "\n".join([f"• {note}" for note in notes])
    await message.answer(f"<b>📝 Твои заметки:</b>\n{notes_text}", parse_mode="HTML")

@dp.message(F.text == "🧠 Запомнить что‑то")
async def remember_start(message: types.Message, state: FSMContext):
    await message.answer("Напиши, что нужно запомнить:")
    await state.set_state(DialogStates.waiting_for_note)

@dp.message(DialogStates.waiting_for_note)
async def remember_finish(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    user = get_user_data(user_id)
    note = message.text.strip()
    if "notes" not in user:
        user["notes"] = []
    user["notes"].append(note)
    save_data(data)
    await message.answer(f"✅ Запомнила: <i>{note}</i>", parse_mode="HTML")
    await state.clear()

@dp.message(F.text == "⏰ Напомнить")
async def set_reminder_start(message: types.Message, state: FSMContext):
    await message.answer("Напиши, о чём напомнить и когда (например, «Через 2 часа позвонить маме»):")
    await state.set_state(DialogStates.waiting_for_reminder)

@dp.message(DialogStates.waiting_for_reminder)
async def set_reminder_finish(message: types.Message, state: FSMContext):
    reminder_text = message.text.strip()
    reminders.append({"user_id": str(message.from_user.id), "text": reminder_text, "time": datetime.now()})
    await message.answer(f"⏰ Напоминание установлено: <i>{reminder_text}</i>", parse_mode="HTML")
    await state.clear()

@dp.message(F.text == "🗑️ Очистить заметки")
async def clear_notes(message: types.Message):
    user_id = str(message.from_user.id)
    user = get_user_data(user_id)
    if "notes" in user and user["notes"]:
        user["notes"] = []
        save_data(data)
        await message.answer("✅ Все заметки очищены.")
    else:
        await message.answer("У тебя и так нет заметок для очистки.")


# ================= ПОГОДА =================
@dp.message(F.text == "🌤 Погода")
async def weather_category(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=WEATHER_MENU)

@dp.message(F.text == "📍 Указать город")
async def set_city_start(message: types.Message, state: FSMContext):
    await message.answer("Напиши название города (например, Москва):")
    await state.set_state(DialogStates.waiting_for_city)

@dp.message(DialogStates.waiting_for_city)
async def set_city_finish(message: types.Message, state: FSMContext):
    city = message.text.strip()
    coords = await geocode_city(city)
    if not coords:
        await message.answer("Город не найден. Попробуй ещё раз.")
        return
    lat, lon, city_name = coords
    user_id = str(message.from_user.id)
    user = get_user_data(user_id)
    user["city"] = city_name
    save_data(data)
    await message.answer(f"✅ Город установлен: {city_name}")
    await state.clear()

@dp.message(F.text == "🌤 Сейчас")
async def current_weather(message: types.Message):
    user = get_user_data(message.from_user.id)
    city = user.get("city")
    if not city:
        await message.answer("Сначала укажи свой город кнопкой «📍 Указать город».")
        return
    coords = await geocode_city(city)
    if not coords:
        await message.answer("Не удалось найти координаты города. Попробуй указать заново.")
        return
    lat, lon, city_name = coords
    weather = await fetch_weather(lat, lon)
    if not weather:
        await message.answer("Не удалось получить погоду. Попробуй позже.")
        return

    current = weather["current"]
    temp = current["temperature_2m"]
    code = current["weather_code"]
    wind = current["wind_speed_10m"]

    await message.answer(
        f"<b>Погода в {city_name} сейчас:</b>\n"
        f"Температура: {temp}°C\n"
        f"Состояние: {weather_text(code)}\n"
        f"Ветер: {wind} м/с",
        parse_mode="HTML"
    )

@dp.message(F.text == "🌙 Завтра")
async def tomorrow_weather(message: types.Message):
    user = get_user_data(message.from_user.id)
    city = user.get("city")
    if not city:
        await message.answer("Сначала укажи свой город кнопкой «📍 Указать город».")
        return
    coords = await geocode_city(city)
    if not coords:
        await message.answer("Не удалось найти координаты города. Попробуй указать заново.")
        return
    lat, lon, city_name = coords
    weather = await fetch_weather(lat, lon)
    if not weather:
        await message.answer("Не удалось получить прогноз на завтра. Попробуй позже.")
        return

    daily = weather["daily"]
    max_temp = daily["temperature_2m_max"][0]
    min_temp = daily["temperature_2m_min"][0]
    code = daily["weather_code"][0]

    await message.answer(
        f"<b>Прогноз погоды в {city_name} на завтра:</b>\n"
        f"Максимальная температура: {max_temp}°C\n"
        f"Минимальная температура: {min_temp}°C\n"
        f"Состояние: {weather_text(code)}",
        parse_mode="HTML"
    )

# ================= ИГРЫ =================
@dp.message(F.text == "🎮 Игры")
async def games_category(message: types.Message):
    await message.answer("Выбери игру или факт:", reply_markup=GAMES_MENU)


@dp.message(F.text == "🎯 Случайный факт")
async def random_fact(message: types.Message):
    fact = random.choice(FACTS)
    await message.answer(f"🧠 <i>{fact}</i>", parse_mode="HTML")

@dp.message(F.text == "🎲 Угадай число")
async def guess_number_start(message: types.Message, state: FSMContext):
    number = random.randint(1, 100)
    await state.update_data(secret_number=number)
    await message.answer("Я загадал число от 1 до 100. Попробуй угадать!")
    await state.set_state(DialogStates.waiting_for_guess)


@dp.message(DialogStates.waiting_for_guess)
async def guess_number_check(message: types.Message, state: FSMContext):
    data = await state.get_data()
    secret_number = data.get("secret_number")
    try:
        guess = int(message.text)
        if guess == secret_number:
            await message.answer("🎉 Правильно! Ты угадал!")
            await state.clear()
        elif guess < secret_number:
            await message.answer("Слишком мало! Попробуй ещё.")
        else:
            await message.answer("Слишком много! Попробуй ещё.")
    except ValueError:
        await message.answer("Введи число, пожалуйста.")

# ================= ПРОФИЛЬ =================
@dp.message(F.text == "👤 Профиль")
async def profile(message: types.Message):
    user_id = str(message.from_user.id)
    user = get_user_data(user_id)
    name = user.get("name", message.from_user.first_name)
    city = user.get("city", "не указан")
    bday = user.get("bday", "не установлен")
    await message.answer(
        f"<b>👤 Твой профиль:</b>\n"
        f"Имя: {name}\n"
        f"Город: {city}\n"
        f"День рождения: {bday}",
        parse_mode="HTML"
    )

# ================= ПОМОЩЬ =================
@dp.message(F.text == "ℹ️ Помощь")
async def help_category(message: types.Message):
    await message.answer("Выбери раздел помощи:", reply_markup=HELP_MENU)


@dp.message(F.text == "❓ Как пользоваться")
async def how_to_use(message: types.Message):
    help_text = (
        "🤖 <b>Как пользоваться ботом:</b>\n\n"
        "• <b>Погода</b> — текущая и завтрашняя погода\n"
        "• <b>День рождения</b> — установить ДР и узнать, сколько дней осталось\n"
        "• <b>Заметки и память</b> — сохранять заметки и ставить напоминания\n"
        "• <b>Игры</b> — угадывать числа и получать случайные факты\n"
        "• <b>Профиль</b> — посмотреть свои данные\n"
        "• <b>Поболтать</b> — просто пообщаться"
    )
    await message.answer(help_text, parse_mode="HTML")


@dp.message(F.text == "📚 Команды")
async def commands_list(message: types.Message):
    commands_text = (
        "📝 <b>Список команд:</b>\n\n"
        "/start — запустить бота и увидеть главное меню\n"
        "Все остальные действия — через кнопки меню"
    )
    await message.answer(commands_text, parse_mode="HTML")

# ================= БОЛТОВНЯ =================
@dp.message(F.text == "💬 Поболтать")
async def chat_button(message: types.Message):
    chat_users.add(str(message.from_user.id))
    await message.answer(
        "💬 Режим болтовни включён! Теперь отвечаю на любое сообщение.\n"
        "Выйти — кнопка «🏠 Главное меню»."
    )

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Удаляем предыдущее сообщение с клавиатурой, если оно есть
    await message.delete()
    user = get_user_data(message.from_user.id)
    name = user.get("name", message.from_user.first_name)
    greeting = (
        f"<b>{get_time_of_day()}, {name}! 👋</b>\n\n"
        f"Я твой помощник: погода, факты, игры, заметки, напоминания и просто поболтать.\n\n"
        f"<i>Выбери категорию из меню ниже 👇</i>"
    )
    # Отправляем новое сообщение с актуальной клавиатурой
    await message.answer(greeting, reply_markup=MAIN_MENU, parse_mode="HTML")


# Обработчик для всех текстовых сообщений вне состояний
@dp.message(F.text & ~F.command)
async def handle_all_messages(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    # Если пользователь в режиме болтовни
    if user_id in chat_users:
        reply = build_chat_reply(user_id, message.text)
        await message.answer(reply, parse_mode="HTML")
    else:
        # Если не в режиме болтовни и не команда меню — возвращаем в главное меню
        await message.answer("Используй кнопки меню 👇", reply_markup=MAIN_MENU)

# ================= ЗАПУСК БОТА =================
async def main():
    bot = Bot(token=TOKEN, parse_mode="HTML")
    dp = Dispatcher()
    dp.include_router(dp)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
