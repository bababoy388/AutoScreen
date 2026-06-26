import configparser
from datetime import datetime
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import ClientTimeout

# Настройка логирования aiogram
logging.basicConfig(level=logging.INFO)

config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')
TOKEN = config.get('Telegram', 'token')

# Явно создаём сессию с таймаутом (даже без прокси)
timeout = ClientTimeout(total=30)
session = AiohttpSession(timeout=timeout)

bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

# ========== Вспомогательная функция для работы с конфигом ==========
CONFIG_PATH = 'config.ini'

def read_config():
    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_PATH, encoding='utf-8')
    return config

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        config.write(f)

# ========== Команда /info ==========
@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    config = read_config()
    if 'Schedule' not in config:
        await message.answer("Секция [Schedule] отсутствует в конфиге.")
        return
    schedule = config['Schedule']
    mode = schedule.get('mode', 'не указан')
    time_val = schedule.get('time', 'не указано')
    interval = schedule.get('interval_minutes', 'не указан')
    text = (
        f"📋 Текущие настройки расписания:\n"
        f"Режим: {mode}\n"
        f"Время: {time_val}\n"
        f"Интервал (мин): {interval}"
    )
    await message.answer(text)

# ========== Команда /daily ==========
@dp.message(Command("daily"))
async def cmd_daily(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Укажите время в формате HH:MM, например: /daily 12:30")
        return
    time_str = parts[1].strip()
    try:
        datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await message.reply("❌ Неверный формат времени. Используйте HH:MM (например, 14:30)")
        return

    config = read_config()
    if 'Schedule' not in config:
        config['Schedule'] = {}
    config['Schedule']['time'] = time_str
    save_config(config)

    await message.reply(f"✅ Время ежедневного запуска обновлено на {time_str}")

# ========== Команда /interval_m ==========
@dp.message(Command("interval_m"))
async def cmd_interval_m(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Укажите количество минут, например: /interval_m 120")
        return
    minutes_str = parts[1].strip()
    try:
        minutes = int(minutes_str)
        if minutes <= 0:
            raise ValueError
    except ValueError:
        await message.reply("❌ Введите положительное целое число минут.")
        return

    config = read_config()
    if 'Schedule' not in config:
        config['Schedule'] = {}
    config['Schedule']['interval_minutes'] = str(minutes)
    save_config(config)

    await message.reply(f"✅ Интервал обновлён на {minutes} минут")

# ========== (Заготовка) Команда /get_graph ==========
@dp.message(Command("get_graph"))
async def cmd_get_graph(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Укажите количество минут назад, например: /get_graph 60")
        return
    try:
        n = int(parts[1].strip())
        if n <= 0:
            raise ValueError
    except ValueError:
        await message.reply("❌ Введите положительное целое число минут.")
        return

    # Здесь позже будет генерация графика за последние n минут
    await message.reply(f"⏳ Генерация графика за последние {n} минут... (пока не реализовано)")

@dp.message()
async def debug_all_messages(message: types.Message):
    print(f"[DEBUG] Сообщение получено: '{message.text}' от {message.from_user.id} в чате {message.chat.id}")
    await message.reply("Я получил ваше сообщение!")