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
TOKEN = "9247024816:AAHT4THlYvjsJ16DiC70ZYqdSR_4xTeHPo0"

if "ВСТАВЬ" in TOKEN or len(TOKEN) < 20:
    print("=" * 55)
    print("Сначала вставь токен!")
    print("Открой bot.py в Блокноте и замени")
    print("ВСТАВЬ_СЮДА_СВОЙ_ТОКЕН на токен от @BotFather.")
    print("=" * 55)
    input("Нажми Enter для выхода... ")
    raise SystemExit

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


# ================= КНОПКИ =================
BTN_MENU = "🏠 Меню"
BTN_WEATHER = "🌤 Погода сейчас"
BTN_TOMORROW = "🌙 Погода на завтра"
BTN_SET_CITY = "📍 Указать город"
BTN_PROFILE = "👤 Мой профиль"
BTN_BDAY = "🎂 Отчёт до ДР"
BTN_SET_BDAY = "📅 Установить ДР"
BTN_GAME = "🎲 Угадай число"
BTN_FACT = "🎯 Случайный факт"
BTN_NOTES = "📝 Мои заметки"
BTN_REMEMBER = "🧠 Запомнить что-то"
BTN_REMIND = "⏰ Напомнить"
BTN_IDEAS = "💡 Идеи"
BTN_CHAT = "💬 Поболтать"


def main_menu():
    kb = [
        [KeyboardButton(text=BTN_WEATHER), KeyboardButton(text=BTN_TOMORROW)],
        [KeyboardButton(text=BTN_SET_CITY), KeyboardButton(text=BTN_PROFILE)],
        [KeyboardButton(text=BTN_BDAY), KeyboardButton(text=BTN_SET_BDAY)],
        [KeyboardButton(text=BTN_GAME), KeyboardButton(text=BTN_FACT)],
        [KeyboardButton(text=BTN_NOTES), KeyboardButton(text=BTN_REMEMBER)],
        [KeyboardButton(text=BTN_REMIND), KeyboardButton(text=BTN_IDEAS)],
        [KeyboardButton(text=BTN_CHAT)],
        [KeyboardButton(text=BTN_MENU)],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


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
        history.pop(0)
    return repeat_note + get_topic_reply(user_text)


# ================= БОТ =================
bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_user_data(message.from_user.id)
    name = user.get("name", message.from_user.first_name)
    greeting = (
        f"{get_time_of_day()}, {name}! 👋\n"
        "Я твой помощник: погода, факты, игра, заметки, напоминания, идеи и просто поболтать."
    )
    await message.answer(greeting, reply_markup=main_menu())


@dp.message(F.text == BTN_MENU)
async def menu_button(message: types.Message, state: FSMContext):
    chat_users.discard(str(message.from_user.id))
    await state.clear()
    await message.answer("Открыла меню 👇", reply_markup=main_menu())


# ---------- Погода ----------
@dp.message(F.text == BTN_WEATHER)
async def weather_now(message: types.Message):
    user = get_user_data(message.from_user.id)
    if "lat" not in user:
        await message.answer("Сначала укажи город кнопкой «📍 Указать город».")
        return
    await message.answer("Смотрю погоду… 🔎")
    result = await fetch_weather(user["lat"], user["lon"])
    if not result or "current" not in result:
        await message.answer("Не смогла достать погоду. Попробуй позже 🙏")
        return
    cur = result["current"]
    desc = weather_text(cur.get("weather_code", -1))
    temp = cur.get("temperature_2m")
    wind = cur.get("wind_speed_10m")
    await message.answer(
        f"🌤 Погода сейчас в {user['city']}:\n"
        f"{desc}\n"
        f"🌡 Температура: {temp}°C\n"
        f"💨 Ветер: {wind} км/ч"
    )


@dp.message(F.text == BTN_TOMORROW)
async def weather_tomorrow(message: types.Message):
    user = get_user_data(message.from_user.id)
    if "lat" not in user:
        await message.answer("Сначала укажи город кнопкой «📍 Указать город».")
        return
    await message.answer("Смотрю прогноз на завтра… 🔎")
    result = await fetch_weather(user["lat"], user["lon"])
    if not result or "daily" not in result:
        await message.answer("Не смогла достать прогноз. Попробуй позже 🙏")
        return
    daily = result["daily"]
    if len(daily.get("time", [])) < 2:
        await message.answer("Прогноз на завтра пока недоступен 🤷")
        return
    tomorrow = daily["time"][1]
    t_max = daily["temperature_2m_max"][1]
    t_min = daily["temperature_2m_min"][1]
    desc = weather_text(daily["weather_code"][1])
    await message.answer(
        f"🌙 Прогноз на завтра ({tomorrow}) в {user['city']}:\n"
        f"{desc}\n"
        f"🌡 От {t_min}°C до {t_max}°C"
    )


@dp.message(F.text == BTN_SET_CITY)
async def set_city_start(message: types.Message, state: FSMContext):
    await state.set_state(DialogStates.waiting_for_city)
    await message.answer("Напиши название города (например, Новосибирск), я запомню его для погоды.")


@dp.message(DialogStates.waiting_for_city)
async def set_city_finish(message: types.Message, state: FSMContext):
    city = message.text.strip()
    if not city:
        await message.answer("Город не может быть пустым. Напиши название ещё раз.")
        return
    geo = await geocode_city(city)
    if geo is None:
        await message.answer("Не нашла такой город 🤔 Попробуй написать ещё раз (например, «Новосибирск»).")
        return
    lat, lon, name = geo
    user = get_user_data(message.from_user.id)
    user["city"] = name
    user["lat"] = lat
    user["lon"] = lon
    save_data(data)
    await state.clear()
    await message.answer(f"✅ Город {name} сохранён! Теперь погода будет по нему.")


# ---------- Профиль и день рождения ----------
@dp.message(F.text == BTN_PROFILE)
async def profile(message: types.Message):
    user = get_user_data(message.from_user.id)
    name = user.get("name", message.from_user.first_name)
    city = user.get("city", "не указан")
    bday = user.get("bday", "не установлен")
    await message.answer(f"👤 Твой профиль:\nИмя: {name}\nГород: {city}\nДень рождения: {bday}")


@dp.message(F.text == BTN_BDAY)
async def bday_report(message: types.Message):
    user = get_user_data(message.from_user.id)
    bday_str = user.get("bday")
    if not bday_str:
        await message.answer("День рождения не установлен. Нажми «📅 Установить ДР», чтобы добавить дату.")
        return
    try:
        bday = datetime.strptime(bday_str, "%Y-%m-%d")
        today = datetime.today()
        next_bday = bday.replace(year=today.year)
        if next_bday < today:
            next_bday = next_bday.replace(year=next_bday.year + 1)
        days_left = (next_bday - today).days
        name = user.get("name", message.from_user.first_name)
        await message.answer(f"🎂 До дня рождения {name} осталось {days_left} дней!")
    except Exception:
        await message.answer("Ошибка в дате. Установи день рождения заново.")


@dp.message(F.text == BTN_SET_BDAY)
async def set_bday_start(message: types.Message, state: FSMContext):
    await state.set_state(DialogStates.waiting_for_bday)
    await message.answer("Введи дату дня рождения в формате ГГГГ-ММ-ДД (например, 2005-03-15).")


@dp.message(DialogStates.waiting_for_bday)
async def set_bday_finish(message: types.Message, state: FSMContext):
    date_str = message.text.strip()
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await message.answer("Неверный формат. Попробуй ещё раз: ГГГГ-ММ-ДД.")
        return
    user = get_user_data(message.from_user.id)
    user["bday"] = date_str
    save_data(data)
    await state.clear()
    await message.answer(f"✅ День рождения {date_str} сохранён!")


# ---------- Игра ----------
@dp.message(F.text == BTN_GAME)
async def start_game(message: types.Message, state: FSMContext):
    secret = random.randint(1, 100)
    await state.update_data(secret=secret, tries=0)
    await state.set_state(DialogStates.waiting_for_guess)
    await message.answer("🎮 Я загадала число от 1 до 100. Попробуй угадать! Напиши число.")


@dp.message(DialogStates.waiting_for_guess)
async def process_guess(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Напиши число от 1 до 100 🙂 (или «🏠 Меню», чтобы выйти)")
        return
    info = await state.get_data()
    secret = info["secret"]
    tries = info["tries"] + 1
    guess = int(text)
    if guess < secret:
        await state.update_data(tries=tries)
        await message.answer("Больше ⬆️")
    elif guess > secret:
        await state.update_data(tries=tries)
        await message.answer("Меньше ⬇️")
    else:
        await message.answer(f"🎉 Угадала! Это было {secret}. Попыток: {tries}")
        await state.clear()


# ---------- Факты ----------
@dp.message(F.text == BTN_FACT)
async def send_fact(message: types.Message):
    fact = random.choice(FACTS)
    await message.answer(f"🎯 Вот факт: {fact}")


# ---------- Заметки ----------
@dp.message(F.text == BTN_REMEMBER)
async def remember_start(message: types.Message, state: FSMContext):
    await state.set_state(DialogStates.waiting_for_note)
    await message.answer("Напиши, что нужно запомнить (коротко). Например: «Купить хлеб».")


@dp.message(DialogStates.waiting_for_note)
async def save_note(message: types.Message, state: FSMContext):
    note = message.text.strip()
    if not note:
        await message.answer("Пустая заметка не сохранится 🙂 Напиши что-нибудь.")
        return
    uid = str(message.from_user.id)
    data.setdefault("notes", {}).setdefault(uid, []).append(note)
    save_data(data)
    await state.clear()
    await message.answer(f"✅ Запомнила: «{note}»")


@dp.message(F.text == BTN_NOTES)
async def show_notes(message: types.Message):
    uid = str(message.from_user.id)
    notes = data.get("notes", {}).get(uid, [])
    if not notes:
        await message.answer("Пока нет заметок. Хочешь что-нибудь сохранить? 😉")
        return
    lines = "\n".join(f"- {n}" for n in notes[-10:])
    await message.answer("📝 Твои заметки:\n" + lines)


# ---------- Напоминания ----------
@dp.message(F.text == BTN_REMIND)
async def remind_start(message: types.Message, state: FSMContext):
    await state.set_state(DialogStates.waiting_for_reminder)
    await message.answer("Напиши напоминание в формате:\n18:30 полить цветы")


@dp.message(DialogStates.waiting_for_reminder)
async def save_reminder(message: types.Message, state: FSMContext):
    text = message.text.strip()
    parts = text.split(" ", 1)
    if len(parts) != 2:
        await message.answer("Не поняла формат 🤔 Напиши так:\n18:30 полить цветы")
        return
    time_str, note = parts[0], parts[1].strip()
    try:
        datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await message.answer("Время не похоже на ЧЧ:ММ 🤔 Попробуй ещё раз.")
        return
    reminders.append({"uid": message.from_user.id, "time": time_str, "text": note})
    await state.clear()
    await message.answer(f"✅ Запомнила! Напомню в {time_str}: {note}")


# ---------- Идеи ----------
@dp.message(F.text == BTN_IDEAS)
async def ideas_menu(message: types.Message, state: FSMContext):
    await state.set_state(DialogStates.waiting_for_idea_topic)
    await message.answer(
        "💡 Напиши тему для идей. Доступные темы:\n"
        "— «подарок»\n"
        "— «название»\n"
        "— «что приготовить»"
    )


@dp.message(DialogStates.waiting_for_idea_topic)
async def send_ideas(message: types.Message, state: FSMContext):
    topic = message.text.strip().lower()
    ideas = IDEA_BANK.get(topic, [])
    if not ideas:
        await message.answer(
            "Пока нет идей для этой темы 🤔 Попробуй одну из тем: «подарок», «название», «что приготовить»."
        )
        await state.clear()
        return
    result = "\n".join(f"• {i}" for i in random.sample(ideas, min(3, len(ideas))))
    await message.answer(f"💡 Вот идеи по теме «{topic}»:\n{result}")
    await state.clear()


# ---------- Болтовня ----------
@dp.message(F.text == BTN_CHAT)
async def chat_button(message: types.Message):
    chat_users.add(str(message.from_user.id))
    await message.answer(
        "💬 Режим болтовни включён! Теперь отвечаю на любое сообщение.\n"
        "Выйти — кнопка «🏠 Меню»."
    )


# ---------- Эхо (всегда последним) ----------
@dp.message()
async def echo(message: types.Message):
    if not message.text:
        return
    uid = str(message.from_user.id)
    if uid in chat_users:
        await message.answer(build_chat_reply(uid, message.text))
        return
    if len(message.text) > 3900:
        await message.answer("Слишком длинное сообщение 🙂 Пришли покороче или раздели на части.")
        return
    await message.answer(message.text)


# ================= ЗАПУСК =================
async def reminder_loop():
    while True:
        now = datetime.now().strftime("%H:%M")
        for r in list(reminders):
            if r["time"] == now:
                try:
                    await bot.send_message(r["uid"], f"⏰ Напоминание: {r['text']}")
                except Exception:
                    pass
                reminders.remove(r)
        await asyncio.sleep(15)


async def main():
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("Ошибка запуска:", e)
        input("Нажми Enter для выхода... ")
