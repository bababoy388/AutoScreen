import configparser
from datetime import datetime, timedelta, timezone
import re
from collections import defaultdict
import logging

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile

from core.parser import Parser
from core.builder_graph import PlotConfig
from core.tools import log_error

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== Чтение конфига ==========
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')
TOKEN = config.get('Telegram', 'token')
PROXY = config.get('Telegram', 'proxy', fallback=None)
if PROXY == '':
    PROXY = None

# ========== Создаём сессию с прокси (если нужен) ==========
session = AiohttpSession(proxy=PROXY) if PROXY else AiohttpSession()

# ========== Создаём объекты бота и диспетчера ==========
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

# ========== Вспомогательные функции для работы с конфигом ==========
CONFIG_PATH = 'config.ini'

def read_config():
    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_PATH, encoding='utf-8')
    return config

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        config.write(f)

# ========== Команда /start ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 Привет! Я бот для мониторинга мельниц.\n"
        "Используй /help для списка команд."
    )

# ========== Команда /help ==========
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "📋 Доступные команды:\n"
        "/start - приветствие\n"
        "/help - список команд\n"
        "/info - показать текущие настройки расписания\n"
        "/mode daily|interval|once - переключить режим работы\n"
        "/daily HH:MM - установить время для ежедневного запуска\n"
        "/interval_m N - установить интервал в минутах\n"
        "/get_graph N [дата] - получить график за N минут до указанной даты (или сейчас)\n"
        "Пример: /get_graph 720\n"
        "Или: /get_graph 720 2026.06.24-14:00"
    )
    await message.answer(text)

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

# ========== Команда /mode ==========
@dp.message(Command("mode"))
async def cmd_mode(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Укажите режим: daily, interval или once, например: /mode interval")
        return
    mode = parts[1].strip().lower()
    if mode not in ('daily', 'interval', 'once'):
        await message.reply("❌ Доступные режимы: once, daily, interval")
        return
    config = read_config()
    if 'Schedule' not in config:
        config['Schedule'] = {}
    config['Schedule']['mode'] = mode
    save_config(config)
    await message.reply(f"✅ Режим изменён на {mode}")

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

# ========== Команда /get_graph ==========
def parse_graph_args(text: str):
    """
    Парсит текст команды /get_graph.
    Возвращает (minutes, right_dt) или (None, None) при ошибке.
    """
    # Убираем упоминание бота, если есть
    clean = re.sub(r'^/get_graph(@\w+)?', '', text).strip()
    parts = clean.split(maxsplit=1)
    if not parts:
        return None, None
    try:
        minutes = int(parts[0])
        if minutes <= 0:
            return None, None
    except ValueError:
        return None, None

    right_dt = None
    if len(parts) > 1:
        date_str = parts[1].strip()
        # Пробуем парсить формат ГГГГ.ММ.ДД-ЧЧ:ММ
        try:
            right_dt = datetime.strptime(date_str, '%Y.%m.%d-%H:%M')
        except ValueError:
            return None, None
        # Делаем timezone-aware (локальное время)
        right_dt = right_dt.astimezone()
    return minutes, right_dt

@dp.message(F.text.regexp(r'^/get_graph(@\w+)?( .+)?$'))
async def cmd_get_graph(message: types.Message):
    minutes, right_dt = parse_graph_args(message.text)
    if minutes is None:
        await message.reply(
            "❌ Используйте: /get_graph <минуты> [дата в формате ГГГГ.ММ.ДД-ЧЧ:ММ]\n"
            "Пример: /get_graph 720\n"
            "Или: /get_graph 720 2026.06.24-14:00"
        )
        return

    # Определяем правую границу интервала
    if right_dt is None:
        right_dt = datetime.now().astimezone()
    else:
        if right_dt > datetime.now().astimezone():
            await message.reply("❌ Указанная дата в будущем. Используйте прошедшее время.")
            return

    from_dt = right_dt - timedelta(minutes=minutes)
    time_range_str = f"{from_dt.strftime('%Y-%m-%d %H:%M')} – {right_dt.strftime('%Y-%m-%d %H:%M')}"
    await message.reply(f"⏳ Строю графики за период: {time_range_str}")

    # Читаем конфиг
    config = read_config()
    # Группируем секции (аналогично main.py)
    groups = defaultdict(list)
    for section in config.sections():
        if section.startswith('Plot_'):
            key = (
                config.get(section, 'millUuid'),
                config.get(section, 'info_host'),
                config.get(section, 'info_port'),
                config.get(section, 'download_host'),
                config.get(section, 'download_port'),
                config.getint(section, 'from_minutes'),
                config.getint(section, 'to_minutes')
            )
            groups[key].append(section)
        elif section.startswith('Subplot_'):
            sections_str = config.get(section, 'sections')
            first_plot = sections_str.split(',')[0].strip()
            if config.has_section(first_plot):
                key = (
                    config.get(first_plot, 'millUuid'),
                    config.get(first_plot, 'info_host'),
                    config.get(first_plot, 'info_port'),
                    config.get(first_plot, 'download_host'),
                    config.get(first_plot, 'download_port'),
                    config.getint(first_plot, 'from_minutes'),
                    config.getint(first_plot, 'to_minutes')
                )
                groups[key].append(section)
            else:
                log_error(f"Секция {first_plot} не найдена для сабплота {section}")

    if not groups:
        await message.reply("❌ В конфиге нет секций Plot_ или Subplot_ для построения графиков.")
        return

    plotter = PlotConfig('config.ini')
    sent_count = 0
    for key, sections in groups.items():
        mill_uuid, info_host, info_port, download_host, download_port, _, _ = key
        try:
            # Создаём парсер с параметрами из конфига (временные границы переопределим)
            parser = Parser(
                mill_uuid=mill_uuid,
                from_minutes=0,
                to_minutes=0,
                host_info=info_host,
                port_info=info_port,
                host_download=download_host,
                port_download=download_port
            )
            # Устанавливаем кастомные временные границы напрямую
            def fmt(dt):
                dt_utc = dt.astimezone(timezone.utc)
                return dt_utc.strftime('%Y-%m-%dT%H:%M:%S.') + f"{dt_utc.microsecond // 1000:03d}Z"
            parser.from_time = fmt(from_dt)
            parser.to_time = fmt(right_dt)
            parser.from_local = from_dt
            parser.to_local = right_dt

            df = parser.get_dataframe()
            if df.empty:
                await message.reply(f"⚠️ Нет данных для группы {mill_uuid} за указанный период.")
                continue

            for section in sections:
                if section.startswith('Plot_'):
                    if config.getboolean(section, 'upload', fallback=True):
                        saved_path = plotter.build_for_section(section, df)
                        if saved_path:
                            photo = FSInputFile(saved_path)
                            caption = f"{section}\n{time_range_str}"
                            await message.answer_photo(photo, caption=caption)
                            sent_count += 1
                elif section.startswith('Subplot_'):
                    saved_path = plotter.build_subplot(section, df)
                    if saved_path:
                        photo = FSInputFile(saved_path)
                        caption = f"{section}\n{time_range_str}"
                        await message.answer_photo(photo, caption=caption)
                        sent_count += 1
        except Exception as e:
            log_error(f"Ошибка при обработке группы {key}: {e}")
            await message.reply(f"❌ Ошибка при загрузке данных для {mill_uuid}: {e}")

    if sent_count == 0:
        await message.reply("❌ Не удалось построить ни одного графика.")
    else:
        await message.reply(f"✅ Отправлено {sent_count} графиков.")

# ========== Обработчик не-команд ==========
@dp.message(F.text & ~F.text.startswith('/'))
async def echo(message: types.Message):
    await message.reply("Я понимаю только команды. Используйте /help для списка.")