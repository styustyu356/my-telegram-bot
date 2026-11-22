import asyncio
import json
import logging
import random
import os
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# ================= НАСТРОЙКИ =================
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


# ================= УЛУЧШЕННОЕ МЕНЮ =================
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌤 Погода"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="💡 Идеи и заметки")],
        [KeyboardButton(text="💬 Поболтать"), KeyboardButton(text="ℹ️ Помощь")],
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

IDEAS_NOTES_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💡 Идеи"), KeyboardButton(text="📝 Заметки")],
        [KeyboardButton(text="🧠 Запомнить"), KeyboardButton(text="⏰ Напомнить")],
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
        0: "ясно ☀️",
        1: "преимущественно ясно 🌤",
        2: "переменная облачность ⛅",
        3: "пасмурно ☁️",
        45: "туман 🌫",
        48: "изморозь 🌫",
        51: "небольшая морось 🌦",
        53: "морось 🌦",
        55: "сильная морось 🌧",
        61: "небольшой дождь 🌦",
        63: "дождь 🌧",
        65: "сильный дождь 🌧",
        71: "небольшой снег 🌨",
        73: "снег 🌨",
        75: "сильный снег ❄️",
        77: "снежные зёрна 🌨",
        80: "ливень 🌦",
        81: "ливень 🌧",
        82: "сильный ливень ⛈",
        85: "снежный ливень 🌨",
        86: "сильный снежный ливень ❄️",
        95: "гроза ⛈",
        96: "гроза с градом ⛈",
        99: "сильная гроза с градом ⛈",
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
        "latitude": lat,
        "longitude": lon,
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

# ================= ФАКТЫ =================
FACTS = [
    "У осьминога три сердца: одно качает кровь по телу, а два других — к жабрам.",
    "Луна удаляется от Земли примерно на 3,8 см в год — через миллиарды лет она будет казаться меньше.",
    "В человеческом мозге около 86 миллиардов нейронов, и каждый связан с тысячами других.",
    "Пчёлы умеют танцевать, чтобы показать другим, где находится нектар.",
    "Самая высокая гора в Солнечной системе — вулкан Олимп на Марсе: он в 3 раза выше Эвереста.",
    "У кошек есть «второй нос» — орган Якобсона, который помогает им «пробовать» запахи.",
    "На Венере день длиннее года: оборот вокруг оси занимает 243 земных дня, а год — 225 дней.",
    "Медузы могут быть бессмертными: вид Turritopsis dohrnii умеет возвращаться из взрослой стадии в молодую.",
    "Слоны узнают себя в зеркале — это признак самосознания, редкий среди животных.",
    "Молния горячее поверхности Солнца: её температура может достигать 30 000 °C.",
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
        "Смузи из банана, овсянки и молока — завтрак за 3 минуты.",
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
        history.pop(0)
    return repeat_note + get_topic_reply(user_text)


# ================= БОТ =================
bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_user_data(message.from_user.id)
    name = user.get("name", message.from_user.first_name)
    greeting = (
        f"<b>{get_time_of_day()}, {name}! 👋</b>\n\n"
        f"Я твой помощник: погода, факты, игры, заметки, напоминания, идеи и просто поболтать.\n\n"
        f"<i>Выбери категорию из меню ниже 👇</i>"
    )
    await message.answer(greeting, reply_markup=MAIN_MENU)


# ---------- ГЛАВНОЕ МЕНЮ ----------
@dp.message(F.text == "🌤 Погода")
async def weather_category(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=WEATHER_MENU)


@dp.message(F.text == "👤 Профиль")
async def profile(message: types.Message):
    user = get_user_data(message.from_user.id)
    name = user.get("name", message.from_user.first_name)
    city = user.get("city", "не указан")
    bday = user.get("bday", "не установлен")
    await message.answer(f"<b>👤 Твой профиль:</b>\nИмя: {name}\nГород: {city}\nДень рождения: {bday}")


@dp.message(F.text == "🎮 Игры")
async def games_category(message: types.Message):
    await message.answer("Выбери игру или факт:", reply_markup=GAMES_MENU)


@dp.message(F.text == "💡 Идеи и заметки")
async def ideas_notes_category(message: types.Message):
    await message.answer("Выбери действие:", reply_markup=IDEAS_NOTES_MENU)


@dp.message(F.text == "💬 Поболтать")
async def chat_button(message: types.Message):
    chat_users.add(str(message.from_
