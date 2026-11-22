import asyncio
import json
import logging
import random
import os
import re
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


# ================= НАСТРОЙКИ =================
import os
TOKEN = os.environ.get("TOKEN")


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
chat_users = set()      # кто сейчас в режиме болтовни
chat_history = {}       # uid -> последние сообщения пользователя


# ================= СОСТОЯНИЯ =================
class DialogStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_bday = State()
    waiting_for_note = State()
    waiting_for_guess = State()
    waiting_for_reminder = State()
    waiting_for_idea_topic = State()


# ================= МЕНЮ =================
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌤 Погода"), KeyboardButton(text="🎂 День рождения")],
        [KeyboardButton(text="📝 Заметки"), KeyboardButton(text="🎮 Игры")],
        [KeyboardButton(text="💡 Идеи и болтовня"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏠 Меню")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выбери категорию 👇",
    is_persistent=True
)

WEATHER_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌤 Сейчас"), KeyboardButton(text="🌙 Завтра")],
        [KeyboardButton(text="📍 Указать город"), KeyboardButton(text="↩️ Назад")],
    ],
    resize_keyboard=True
)

BDAY_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Установить ДР"), KeyboardButton(text="🎂 Дней до ДР")],
        [KeyboardButton(text="↩️ Назад")],
    ],
    resize_keyboard=True
)

NOTES_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Мои заметки"), KeyboardButton(text="🧠 Запомнить что‑то")],
        [KeyboardButton(text="⏰ Напомнить"), KeyboardButton(text="🗑️ Очистить заметки")],
        [KeyboardButton(text="↩️ Назад")],
    ],
    resize_keyboard=True
)

GAMES_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎲 Угадай число"), KeyboardButton(text="🎯 Случайный факт")],
        [KeyboardButton(text="↩️ Назад")],
    ],
    resize_keyboard=True
)

IDEAS_CHAT_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💡 Идеи"), KeyboardButton(text="💬 Поболтать")],
        [KeyboardButton(text="↩️ Назад")],
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
        3: "пасмурно ☁️", 45: "туман 🌫", 48: "изморозь 🌫", 51: "небольшая морось 🌦",
        53: "морось 🌦", 55: "сильная морось 🌧", 61: "небольшой дождь 🌦",
        63: "дождь 🌧", 65: "сильный дождь 🌧", 71: "небольшой снег 🌨",
        73: "снег 🌨", 75: "сильный снег ❄️", 77: "снежные зёрна 🌨",
        80: "ливень 🌦", 81: "ливень 🌧", 82: "сильный ливень ⛈",
        85: "снежный ливень 🌨", 86: "сильный снежный ливень ❄️",
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

# ================= ПРАЗДНИКИ (API + ЗАПАС) =================
async def get_holidays_today():
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")

    # Пробуем взять реальные праздники с calend.ru (сайт, без ключей)
    url = f"https://calend.ru/day/{date_str}/"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    text = await resp.text()
            matches = re.findall(r"<h3[^>]*>(.*?)</h3>", text, re.DOTALL)
            holidays = []
            for m in matches:
                clean = re.sub(r"<[^>]+>", "", m).strip()
                if clean and len(clean) > 5:
                    holidays.append(clean)
            if holidays:
                return holidays
    except Exception:
        pass

    # Запасной вариант: шуточные праздники из внутреннего списка
    backup_holidays = [
        "День спонтанного чаепития ☕",
        "День, когда всё получается 🎉",
        "День объятий с котом 🐱",
        "День забытых дел (самое время доделать!) 📋",
        "День новых начинаний ✨",
        "День ленивого отдыха 😴",
        "День вкусной еды 🍕",
        "День хороших новостей 😊",
        "День, когда можно всё (почти) 💫",
        "День благодарности себе 💖",
    ]
    return random.sample(backup_holidays, min(3, len(backup_holidays)))


# ================= ФАКТЫ =================
FACTS = [
    "У осьминога три сердца: одно качает кровь по телу, а два других — к жабрам.",
    "Луна удаляется от Земли примерно на 3,8 см в год — через миллиарды лет она будет казаться меньше.",
    "В человеческом мозге около 86 миллиардов нейронов, и каждый связан с тысячами других.",
    "Пчёлы умеют танцевать, чтобы показать другим, где находится нектар.",
    "Самая высокая гора в Солнечной системе — вулкан Олимп на Марсе: он в 3 раза выше Эвереста.",
    "У кошек есть «второй нос» — орган Якобсона, который помогает им «пробовать» запахи.",
    "На Венере день длиннее года: оборот вокруг оси занимает 243 земных дня, а год — 225 дней.",
    "Медузы могут быть бессмертными: вид Turritopsis dohrnii умеет возвращаться из взрослой стадии в молодую.",
    "Слоны узнают себя в зеркале — это признак самосознания, редкий среди животных.",
    "Молния горячее поверхности Солнца: её температура может достигать 30 000 °C.",
]

# ================= ИДЕИ (15 вариантов на тему) =================
IDEA_BANK = {
    "подарок": [
        "Кружка с прикольной надписью или принтом.",
        "Сертификат в книжный магазин — угадать сложно, а приятно всегда.",
        "Набор чая или кофе в красивой упаковке.",
        "Уютные носки с забавным узором — банально, но работает.",
        "Блокнот и стильная ручка в наборе.",
        "Ароматическая свеча — классика уюта.",
        "Мини-настольная игра: УНО, дженга, колода карт.",
        "Планер или трекер привычек для новых целей.",
        "Термокружка или стильная бутылка для воды.",
        "Набор сладостей: маршмеллоу, шоколад, зефир.",
        "Мягкий плед — тёплый и приятный на ощупь.",
        "Пазл или магнит с общим фото — трогательно и лично.",
        "Органайзер или косметичка для мелочей.",
        "Подписка на музыку или фильмы на месяц.",
        "Живое растение в горшке — например, суккулент (его сложно загубить).",
    ],
    "название": [
        "«Уютный уголок» — если про дом и комфорт.",
        "«Ветер перемен» — для чего-то динамичного.",
        "«Тихий час» — для отдыха и тишины.",
        "«Точка отсчёта» — для стартапов и планов.",
        "«Свет в окне» — атмосферно и тепло.",
        "«Пятый угол» — про нестандартные идеи.",
        "«За горизонтом» — путешествия и мечты.",
        "«Медленный кофе» — неспешные разговоры о жизни.",
        "«Цифровой сад» — заметки и творчество.",
        "«Голос тишины» — подкаст о внутреннем мире.",
        "«Квадрат луны» — загадочно и стильно.",
        "«Сторож маяка» — поддержка и вдохновение.",
        "«Чайная ложка» — мини-обзоры и рецепты.",
        "«Первая полоса» — новости и мнения.",
        "«Знакомый незнакомец» — истории о людях.",
    ],
    "что приготовить": [
        "Паста с томатами и базиликом — быстро и вкусно.",
        "Омлет с овощами и сыром.",
        "Куриное филе с запечёнными овощами.",
        "Гречка с грибами и луком.",
        "Тост с авокадо и яйцом.",
        "Сырники со сметаной или вареньем.",
        "Ленивые вареники из творога.",
        "Куриный суп с вермишелью — согревает и прост.",
        "Плов с курицей на сковороде.",
        "Запечённый картофель с сыром и зеленью.",
        "Лапша вок с овощами — быстро, как в кафе.",
        "Салат с тунцом, яйцом и свежим огурцом.",
        "Блины с творогом или вареньем.",
        "Творожная запеканка — как в детстве.",
        "Смузи из банана, овсянки и молока — завтрак за 3 минуты.",
    ],
}

# ================= БОЛТОВНЯ =================
TOPIC_REPLIES = {
    ("кот", "кошка", "котёнок", "котенок"): [
        "Обожаю котиков! А у тебя есть свой, или ты коллекционируешь чужие видео с котами? 😸",
        "Котики — бесконечная тема. Расскажи, как зовут твоего, если есть!",
        "Мур-мур. Коты точно правят миром, просто скромничают. А твой какой — полководец или диванный философ?",
    ],
    ("собака", "пёс", "пес", "щенок"): [
        "Собаки — про преданность и прогулки. Какая порода тебе ближе? 🐶",
        "Пёс — лучший друг и лучший будильник одновременно. У тебя есть хвостатый?",
    ],
    ("космос", "планета", "звезда", "луна"): [
        "Космос — вечная загадка. Кстати, Луна каждый год отдаляется от Земли на пару сантиметров — вот это медленный переезд 🌙",
        "Что интереснее: чёрные дыры или поиск жизни на других планетах? Я — за первое, там всё страннее.",
    ],
    ("фильм", "кино", "сериал", "мультфильм"): [
        "Кино — отдельный мир! Что смотришь сейчас? Только без спойлеров, я всё равно не смотрела 😄",
        "А какой последний фильм тебя по-настоящему зацепил?",
    ],
    ("работа", "учёба", "учеба", "школа", "задача", "уроки"): [
        "Понимаю, когда всего много сразу. Может, отвлечёмся на что-нибудь приятное? Например, про еду 🍰",
        "Дела подождут. Расскажи лучше, что хорошего случилось за день?",
    ],
    ("еда", "голоден", "поел", "кушать", "вкусно", "пицца", "суши"): [
        "Ты разогрел мой аппетит! Что сегодня на ужин — что-то быстрое или кулинарный подвиг?",
        "Еда — святое. Что любишь больше: хрустящее или тёплое? А у тебя?",
    ],
    ("привет", "здравствуй", "хай", "ку"): [
        "Привет-привет! Как настроение? Что рассказать — или поболтаем ни о чём?",
        "О, привет! Я как раз разминалась в диалогах. Начинай 🙂",
    ],
    ("скучно", "чем заняться", "не знаю что делать"): [
        "Скучно — значит, пора на поиски приключений! Могу предложить: сыграть в «🎲 Угадай число», узнать факт на «🎯 Случайный факт» или рассказать мне смешную историю из своей жизни.",
        "Держи план спасения от скуки: вспоминаем смешной случай, пьём чай, играем в игру. Что выбираешь?",
    ],
}


UNIVERSAL_REPLIES = [
    "Интересно! Расскажи чуть подробнее 🙂",
    "Ого. А что ты сама об этом думаешь?",
    "Понимаю. И как ты к этому относишься?",
    "Звучит занятно! А что было дальше?",
    "Ага, улавливаю. А почему именно так получилось?",
    "Хм, любопытно. Давай копнём глубже?",
]

def get_topic_reply(text):
    lower = text.lower()
    for words, replies in TOPIC_REPLIES.items():
        if any(w in lower for w in words):
            return random.choice(replies)
    return random.choice(UNIVERSAL_REPLIES)


def build_chat_reply(uid, user_text):
    history = chat_history.setdefault(uid, [])
    repeat_note = ""
    if history:
        prev = history[-1].lower()
        lower = user_text.lower()
        for words in TOPIC_REPLIES:
            if any(w in lower for w in words) and any(w in prev for w in words):
                repeat_note = "Ты снова об этом! Видимо, тема тебе близка 😄\n"
                break
    history.append(user_text)
    if len(history) > 3:
        history.pop(0)  # удаляем самый старый элемент, чтобы история не разрасталась

    reply_text = repeat_note + get_topic_reply(user_text)
    return reply_text


# ================= ОБРАБОТЧИКИ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_user_data(message.from_user.id)
    name = user.get("name", message.from_user.first_name)
    greeting = (
        f"{get_time_of_day()}, {name}! 👋\n"
        "Я твой помощник: погода, факты, игры, заметки, напоминания, идеи и просто поболтать.\n\n"
        "<i>Выбери категорию из меню ниже 👇</i>"
    )
    await message.answer(greeting, reply_markup=MAIN_MENU, parse_mode="HTML")

@dp.message(F.text == "🏠 Меню")
async def menu_button(message: types.Message, state: FSMContext):
    chat_users.discard(str(message.from_user.id))
    await state.clear()
    await message.answer("Открыла меню 👇", reply_markup=MAIN_MENU)


# ---------- ПОГОДА ----------
@dp.message(F.text == "🌤 Погода")
async def weather_category(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=WEATHER_MENU)


@dp.message(F.text == "↩️ Назад")
async def back_to_main(message: types.Message):
    await message.answer("Возвращаюсь в главное меню 👇", reply_markup=MAIN_MENU)


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

@dp.message(F.text == "📍 Указать город")
async def set_city_start(message: types.Message, state: FSMContext):
    await state.set_state(DialogStates.waiting_for_city)
    await message.answer("Напиши название города (например, Москва):")


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


# ---------- ДЕНЬ РОЖДЕНИЯ ----------
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
    today = datetime.now().date()
    bday_date = datetime.strptime(bday, "%d.%m").date().replace(year=today.year)
    if bday_date < today:
        bday_date = bday_date.replace(year=today.year + 1)
    days_left = (bday_date - today).days
    await message.answer(f"🎂 До твоего дня рождения осталось {days_left} дней!")


# ---------- ЗАМЕТКИ ----------
@dp.message(F.text == "📝 Заметки")
async def notes_category(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=NOTES_MENU)

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
    reminders.append({
        "user_id": str(message.from_user.id),
        "text": reminder_text,
        "time": datetime.now()
    })
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


# ---------- ИГРЫ ----------
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


# ---------- ИДЕИ И БОЛТОВНЯ ----------
@dp.message(F.text == "💡 Идеи и болтовня")
async def ideas_chat_category(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=IDEAS_CHAT_MENU)


@dp.message(F.text == "💡 Идеи")
async def ideas_start(message: types.Message, state: FSMContext):
    await message.answer("Напиши тему для идей (например, 'подарок', 'название', 'что приготовить'):")
    await state.set_state(DialogStates.waiting_for_idea_topic)


@dp.message(DialogStates.waiting_for_idea_topic)
async def ideas_finish(message: types.Message, state: FSMContext):
    topic = message.text.lower().strip()
    ideas = IDEA_BANK.get(topic)
    if ideas:
        ideas_text = "\n".join([f"• {idea}" for idea in ideas])
        await message.answer(f"<b>Идеи для '{topic}':</b>\n{ideas_text}", parse_mode="HTML")
    else:
        await message.answer("Не нашёл идей для этой темы. Попробуй другую!")
    await state.clear()


@dp.message(F.text == "💬 Поболтать")
async def chat_start(message: types.Message):
    chat_users.add(str(message.from_user.id))
    await message.answer("💬 Режим болтовни включён! Теперь отвечаю на любое сообщение.\nВыйти — кнопка «🏠 Меню».")


# Обработчик всех текстовых сообщений вне состояний
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


# ---------- ПРОФИЛЬ----------
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

# ================= ЗАПУСК БОТА =================
async def main():
    bot = Bot(token=TOKEN, parse_mode="HTML")
    dp = Dispatcher()
    dp.include_router(dp)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

