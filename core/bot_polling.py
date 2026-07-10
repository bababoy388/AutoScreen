import configparser
from datetime import datetime, timedelta, timezone
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile
from core.parser import Parser
from core.builder_graph import PlotConfig
from core.tools import log_error


config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')
TOKEN = config.get('Telegram', 'token')
CONFIG_PATH = 'config.ini'
session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()


def read_config():
    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_PATH, encoding='utf-8')
    return config

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        config.write(f)

def is_user_allowed(user_id):
    config = read_config()
    ids_str = config.get('AllowedUsers', 'user_ids', fallback='')
    if not ids_str.strip():
        return False
    allowed_ids = [int(x.strip()) for x in ids_str.split(',') if x.strip().isdigit()]
    return user_id in allowed_ids

def parse_graph_args(text):
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
        try:
            right_dt = datetime.strptime(date_str, '%Y.%m.%d-%H:%M')
        except ValueError:
            return None, None
        right_dt = right_dt.astimezone()
    return minutes, right_dt


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    if is_user_allowed(user_id):
        await message.answer(
            "Привет! Я бот для мониторинга мельниц.\n"
            "Используй /help для списка команд.",
        )
    else:
        await message.answer(
            f"Ваш ID: `{user_id}`\n\n"
            f"Для использования бота необходимо, чтобы администратор добавил ваш ID в список разрешённых пользователей.\n"
            f"Отправьте этот ID администратору для добавления.",
            parse_mode="Markdown"
        )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return

    text = (
        "📋 Доступные команды:\n"
        "/start - приветствие / получить свой ID\n"
        "/help - список команд\n"
        "/info - показать текущие настройки расписания\n"
        "/mode [режим] - установить глобальный режим (once/daily/interval)\n"
        "/mode <Subplot> <режим> - установить режим для конкретного завода\n"
        "/daily [время] - установить глобальное время (HH:MM или через запятую)\n"
        "/daily <Subplot> <время> - установить время для конкретного завода\n"
        "/interval_m [минуты] - установить глобальный интервал\n"
        "/interval_m <Subplot> <минуты> - установить интервал для конкретного завода\n"
        "/change_graph <Subplot> <параметр> <значение> - изменить параметр завода\n"
        "/add_user ID - добавить нового пользователя\n"
        "/list_subplots - показать все заводы (сабплоты)\n"
        "/get_graph N [дата] - получить графики за N минут до указанной даты (или сейчас)\n"
        "Пример: /get_graph 720\n"
        "Или: /get_graph 720 2026.06.24-14:00"
    )
    await message.answer(text)

@dp.message(Command("list_subplots"))
async def cmd_list_subplots(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return

    config = read_config()
    subplots = []
    for section in config.sections():
        if section.startswith('Factory_'):
            msg = config.get(section, 'msg', fallback='')
            subplots.append(f"{section} - {msg}")
    if not subplots:
        await message.reply("❌ Сабплоты не найдены.")
        return
    text = "📋 Список заводов:\n" + "\n".join(subplots)
    await message.answer(text)

@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return

    config = read_config()
    # Глобальные настройки
    global_mode = config.get('Schedule', 'mode', fallback='не указан')
    global_time = config.get('Schedule', 'time', fallback='не указано')
    global_interval = config.get('Schedule', 'interval_minutes', fallback='не указан')

    text = "📋 Глобальные настройки расписания:\n"
    text += f"  Режим: {global_mode}\n"
    text += f"  Время: {global_time}\n"
    text += f"  Интервал: {global_interval} мин\n\n"

    subplots = [s for s in config.sections() if s.startswith('Factory_')]
    if subplots:
        text += "📋 Настройки заводов:\n"
        for sub in subplots:
            params = dict(config.items(sub))
            # Определяем, какие параметры переопределены локально
            mode = params.get('mode')
            time_val = params.get('time')
            interval = params.get('interval_minutes')
            # Если локально не задано, пишем "(глобально)"
            mode_str = mode if mode else "(глобально)"
            time_str = time_val if time_val else "(глобально)"
            interval_str = interval if interval else "(глобально)"
            text += f"{sub}:\n"
            text += f"  Режим: {mode_str}\n"
            if mode_str == 'daily':
                text += f"  Время: {time_str}\n"
            elif mode_str == 'interval':
                text += f"  Интервал: {interval_str} мин\n"
            elif mode_str == 'once':
                text += "  (один раз)\n"
            else:
                text += f"  Время: {time_str} / Интервал: {interval_str} мин\n"
    else:
        text += "❌ Секции Factory_ не найдены."

    await message.answer(text)

@dp.message(Command("mode"))
async def cmd_mode(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) == 1:
        await message.reply("❌ Укажите режим: once, daily или interval")
        return
    if len(parts) == 2:
        mode = parts[1].strip().lower()
        if mode not in ('daily', 'interval', 'once'):
            await message.reply("❌ Доступные режимы: once, daily, interval")
            return
        config = read_config()
        config['Schedule']['mode'] = mode
        save_config(config)
        await message.reply(f"✅ Глобальный режим изменён на {mode}")
        return

    if len(parts) == 3:
        subplot = parts[1].strip()
        mode = parts[2].strip().lower()
        if mode not in ('daily', 'interval', 'once'):
            await message.reply("❌ Доступные режимы: once, daily, interval")
            return
        config = read_config()
        if not config.has_section(subplot) or not subplot.startswith('Factory_'):
            await message.reply(f"❌ Секция {subplot} не найдена.")
            return
        config.set(subplot, 'mode', mode)
        save_config(config)
        await message.reply(f"✅ Режим для {subplot} изменён на {mode}")
        return

@dp.message(Command("daily"))
async def cmd_daily(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return
    # Разбиваем только на команду и остаток
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Укажите время (или сабплот и время), например:\n/daily 9:00\n/daily 9:00, 10:00\n/daily Subplot_1 9:00, 10:00")
        return
    args = parts[1].strip()

    # Проверяем, есть ли сабплот (первый токен начинается с "Factory_")
    tokens = args.split(maxsplit=1)
    if len(tokens) == 2 and tokens[0].startswith('Factory_'):
        subplot = tokens[0]
        time_str = tokens[1]
    else:
        subplot = None
        time_str = args

    # Парсим времена: разделяем по запятым, обрезаем пробелы
    times = [t.strip() for t in time_str.split(',') if t.strip()]
    errors = []
    for t in times:
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            errors.append(t)
    if errors:
        await message.reply(f"❌ Неверный формат времени: {', '.join(errors)}. Используйте HH:MM")
        return

    # Преобразуем в строку через запятую для сохранения
    time_str_save = ', '.join(times)

    config = read_config()
    if subplot is None:
        # Глобальное время
        if 'Schedule' not in config:
            config['Schedule'] = {}
        config['Schedule']['time'] = time_str_save
        save_config(config)
        await message.reply(f"✅ Глобальное время обновлено: {time_str_save}")
    else:
        # Локальное время для сабплота
        if not config.has_section(subplot) or not subplot.startswith('Factory_'):
            await message.reply(f"❌ Секция {subplot} не найдена.")
            return
        config.set(subplot, 'time', time_str_save)
        if not config.has_option(subplot, 'mode'):
            config.set(subplot, 'mode', 'daily')
        save_config(config)
        await message.reply(f"✅ Время для {subplot} обновлено: {time_str_save}")

@dp.message(Command("interval_m"))
async def cmd_interval_m(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Укажите количество минут (или сабплот и минуты), например:\n/interval_m 30\n/interval_m Subplot_1 30")
        return
    args = parts[1].strip()
    tokens = args.split(maxsplit=1)
    if len(tokens) == 2 and tokens[0].startswith('Factory_'):
        subplot = tokens[0]
        param = tokens[1].strip()
    else:
        subplot = None
        param = tokens[0].strip()

    if param.lower() == 'global':
        if subplot is None:
            await message.reply("❌ Команда 'global' применима только к локальной настройке (укажите сабплот).")
            return
        config = read_config()
        if not config.has_section(subplot) or not subplot.startswith('Factory_'):
            await message.reply(f"❌ Секция {subplot} не найдена.")
            return
        if config.has_option(subplot, 'interval_minutes'):
            config.remove_option(subplot, 'interval_minutes')
            save_config(config)
            await message.reply(f"✅ Локальный интервал для {subplot} удалён, теперь используется глобальный.")
        else:
            await message.reply(f"ℹ️ В {subplot} не было локального интервала.")
        return

    try:
        minutes = int(param)
        if minutes <= 0:
            raise ValueError
    except ValueError:
        await message.reply("❌ Введите положительное целое число минут.")
        return

    config = read_config()
    if subplot is None:
        # Глобальный интервал
        if 'Schedule' not in config:
            config['Schedule'] = {}
        config['Schedule']['interval_minutes'] = str(minutes)
        save_config(config)
        await message.reply(f"✅ Глобальный интервал обновлён на {minutes} минут")
    else:
        # Локальный интервал для сабплота
        if not config.has_section(subplot) or not subplot.startswith('Factory_'):
            await message.reply(f"❌ Секция {subplot} не найдена.")
            return
        config.set(subplot, 'interval_minutes', str(minutes))
        if not config.has_option(subplot, 'mode'):
            config.set(subplot, 'mode', 'interval')
        save_config(config)
        await message.reply(f"✅ Интервал для {subplot} обновлён на {minutes} минут")

@dp.message(Command("reset_schedule"))
async def cmd_reset_schedule(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Укажите сабплот, например: /reset_schedule Subplot_1")
        return
    subplot = parts[1].strip()
    config = read_config()
    if not config.has_section(subplot) or not subplot.startswith('Factory_'):
        await message.reply(f"❌ Секция {subplot} не найдена.")
        return

    removed = []
    for param in ('mode', 'time', 'interval_minutes'):
        if config.has_option(subplot, param):
            config.remove_option(subplot, param)
            removed.append(param)
    if not removed:
        await message.reply(f"ℹ️ В {subplot} не было локальных настроек расписания.")
        return
    save_config(config)
    await message.reply(f"✅ В {subplot} удалены локальные параметры: {', '.join(removed)}. Теперь используются глобальные настройки.")

@dp.message(Command("add_user"))
async def cmd_add_user(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Укажите ID пользователя, например: /add_user 123456789")
        return
    user_id_str = parts[1].strip()
    if not user_id_str.isdigit():
        await message.reply("❌ ID должен быть числом.")
        return
    new_user_id = int(user_id_str)
    config = read_config()
    ids_str = config.get('AllowedUsers', 'user_ids', fallback='')
    allowed_ids = []
    if ids_str.strip():
        allowed_ids = [int(x.strip()) for x in ids_str.split(',') if x.strip().isdigit()]

    if new_user_id in allowed_ids:
        await message.reply(f"⚠️ Пользователь с ID {new_user_id} уже есть в списке.")
        return

    allowed_ids.append(new_user_id)
    config['AllowedUsers']['user_ids'] = ', '.join(map(str, allowed_ids))
    save_config(config)
    await message.reply(f"✅ Пользователь с ID {new_user_id} добавлен в список разрешённых.")

@dp.message(F.text.regexp(r'^/get_graph(@\w+)?( .+)?$'))
async def cmd_get_graph(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return

    minutes, right_dt = parse_graph_args(message.text)
    if minutes is None:
        await message.reply(
            "❌ Используйте: /get_graph <минуты> [дата в формате ГГГГ.ММ.ДД-ЧЧ:ММ]\n"
            "Пример: /get_graph 720\n"
            "Или: /get_graph 720 2026.06.24-14:00"
        )
        return

    if right_dt is None:
        right_dt = datetime.now().astimezone()
    else:
        if right_dt > datetime.now().astimezone():
            await message.reply("❌ Указанная дата в будущем. Используйте прошедшее время.")
            return

    from_dt = right_dt - timedelta(minutes=minutes)
    time_range_str = f"{from_dt.strftime('%Y-%m-%d %H:%M')} – {right_dt.strftime('%Y-%m-%d %H:%M')}"
    await message.reply(f"⏳ Строю графики за период: {time_range_str}")

    config = read_config()
    subplot_sections = [s for s in config.sections() if s.startswith('Factory_')]
    if not subplot_sections:
        await message.reply("❌ В конфиге нет секций Factory_ для построения графиков.")
        return

    plotter = PlotConfig('config.ini')
    sent_count = 0
    for subplot_section in subplot_sections:
        subplot_params = dict(config.items(subplot_section))
        mill_uuid = subplot_params.get('milluuid')
        host = subplot_params.get('host')
        port = subplot_params.get('port')
        name = subplot_params.get('msg')

        try:
            parser = Parser(
                mill_uuid=mill_uuid,
                host=host,
                port=port,
                from_minutes=0,
                to_minutes=0
            )
            def fmt(dt):
                dt_utc = dt.astimezone(timezone.utc)
                return dt_utc.strftime('%Y-%m-%dT%H:%M:%S.') + f"{dt_utc.microsecond // 1000:03d}Z"
            parser.from_time = fmt(from_dt)
            parser.to_time = fmt(right_dt)
            parser.from_local = from_dt
            parser.to_local = right_dt

            df = parser.get_dataframe()
            if df.empty:
                await message.reply(f"⚠️ Нет данных для {name} за указанный период.")
                continue

            saved_path = plotter.build_subplot(subplot_section, df)
            if saved_path:
                photo = FSInputFile(saved_path)
                name = subplot_params.get('msg', subplot_section)
                caption = f"[{time_range_str}] {name}"
                await message.answer_photo(photo, caption=caption)
                sent_count += 1
        except Exception as e:
            log_error(f"Ошибка при обработке {subplot_section}: {e}")
            await message.reply(f"❌ Ошибка при загрузке данных для {subplot_section}: {e}")