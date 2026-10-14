import configparser
from datetime import datetime, timedelta, timezone
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile, BufferedInputFile
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
    # срезаем /любую_команду и опциональный @botname
    clean = re.sub(r'^/\w+(?:@\w+)?', '', text).strip()
    parts = clean.split()
    if not parts:
        return None, None, None

    # Первый аргумент — минуты (обязательно)
    try:
        minutes = int(parts[0])
        if minutes <= 0:
            return None, None, None
    except ValueError:
        return None, None, None

    right_dt = None
    factory_name = None

    for arg in parts[1:]:
        try:
            dt = datetime.strptime(arg, '%Y.%m.%d-%H:%M')
            right_dt = dt.astimezone()
            continue
        except ValueError:
            pass

        if arg.startswith('Factory_'):
            factory_name = arg
        else:
            return None, None, None

    return minutes, right_dt, factory_name


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

@dp.message(F.text.regexp(r'^/get_raw(@\w+)?( .+)?$'))
async def cmd_get_raw(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        return

    minutes, right_dt, factory_name = parse_graph_args(message.text)
    if minutes is None:
        await message.reply(
            "❌ Используйте: /get_raw <минуты> [дата ГГГГ.ММ.ДД-ЧЧ:ММ] [завод]\n"
            "Примеры:\n"
            "/get_raw 60\n"
            "/get_raw 60 2026.10.14-12:25\n"
            "/get_raw 60 2026.10.14-12:25 Factory_Ozerniy"
        )
        return

    if right_dt is None:
        right_dt = datetime.now().astimezone()
    elif right_dt > datetime.now().astimezone():
        await message.reply("❌ Указанная дата в будущем.")
        return

    from_dt = right_dt - timedelta(minutes=minutes)
    time_range_str = f"{from_dt:%Y-%m-%d %H:%M} – {right_dt:%Y-%m-%d %H:%M}"

    config = read_config()
    all_sections = [s for s in config.sections() if s.startswith('Factory_')]
    if not all_sections:
        await message.reply("❌ В конфиге нет секций Factory_.")
        return

    if factory_name:
        if not config.has_section(factory_name):
            await message.reply(f"❌ Завод '{factory_name}' не найден.")
            return
        sections_to_process = [factory_name]
    else:
        sections_to_process = all_sections

    for section in sections_to_process:
        params = dict(config.items(section))
        name = params.get('msg', section)

        raw_fields_str = params.get('raw_fields', '')
        raw_fields = [f.strip() for f in raw_fields_str.split(',') if f.strip()] or None

        try:
            parser = Parser(
                mill_uuid=params.get('milluuid'),
                host=params.get('host'),
                port=params.get('port'),
                from_minutes=0,
                to_minutes=0,
                raw_fields=raw_fields,
            )
            parser.set_custom_time_range(from_dt, right_dt)

            await message.reply(f"⏳ Скачиваю сырые данные для {name}...")

            # скачивание в отдельном потоке, чтобы не блокировать event loop
            df = await asyncio.to_thread(parser.fetch_raw_data)

            if df.empty:
                await message.reply(f"⚠️ Сырые данные для {name} за {time_range_str} пусты.")
                continue

            csv_bytes = parser.get_raw_data_csv_bytes()
            csv_name = parser.get_raw_data_csv_filename()

            await message.answer_document(
                BufferedInputFile(csv_bytes, filename=f"{section}__{csv_name}"),
                caption=f"RAW [{time_range_str}] {name}",
            )
        except Exception as e:
            log_error(f"Ошибка raw для {section}: {e}")
            await message.reply(f"❌ Ошибка raw для {section}: {e}")

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

    minutes, right_dt, factory_name = parse_graph_args(message.text)
    if minutes is None:
        await message.reply(
            "❌ Используйте: /get_graph <минуты> [дата в формате ГГГГ.ММ.ДД-ЧЧ:ММ] [название завода]\n"
            "Примеры:\n"
            "/get_graph 720\n"
            "/get_graph 720 2026.06.24-14:00\n"
            "/get_graph 720 Factory_Ozerniy\n"
            "/get_graph 720 2026.06.24-14:00 Factory_Ozerniy"
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

    # Получаем список всех секций заводов
    all_sections = [s for s in config.sections() if s.startswith('Factory_')]
    if not all_sections:
        await message.reply("❌ В конфиге нет секций Factory_ для построения графиков.")
        return

    # Если указан конкретный завод — проверяем его существование и фильтруем
    if factory_name:
        if not config.has_section(factory_name):
            await message.reply(f"❌ Завод '{factory_name}' не найден в конфиге.")
            return
        sections_to_process = [factory_name]
    else:
        sections_to_process = all_sections

    plotter = PlotConfig('config.ini')

    for section in sections_to_process:
        subplot_params = dict(config.items(section))
        mill_uuid = subplot_params.get('milluuid')
        host = subplot_params.get('host')
        port = subplot_params.get('port')
        name = subplot_params.get('msg', section)

        try:
            parser = Parser(
                mill_uuid=mill_uuid,
                host=host,
                port=port,
                from_minutes=0,
                to_minutes=0
            )

            # Проверка доступности мельницы
            if not parser.ping():
                await message.reply(f"⚠️ Мельница {name} сейчас недоступна.")
                continue

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

            saved_path = plotter.build_subplot(section, df)
            if saved_path:
                photo = FSInputFile(saved_path)
                caption = f"[{time_range_str}] {name}"
                await message.answer_photo(photo, caption=caption)
                csv_bytes = parser.get_raw_csv_bytes()  # оригинал, не df.to_csv()
                csv_name = parser.get_raw_csv_filename()

                safe_name = f"{section}__{csv_name}"

                await message.answer_document(
                    BufferedInputFile(csv_bytes, filename=safe_name),
                    caption=f"CSV для {name} за период {time_range_str}"
                )
        except Exception as e:
            log_error(f"Ошибка при обработке {section}: {e}")
            await message.reply(f"❌ Ошибка при загрузке данных для {section}: {e}")