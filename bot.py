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
    waiting_for_guess
