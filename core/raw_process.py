"""
Процесс отправки сырых данных.
Запускается как отдельный процесс из main.py.
Полностью самодостаточен: сам читает config.ini, сам считает расписание,
сам качает, строит график через plot_csv_columns.py и отправляет файлы.

График строится по глобальному plot_config.json (один для всех заводов).
Интервал/расписание и период — по каждому заводу (raw_* в Factory_*).
"""
import configparser
import time
from datetime import datetime

from core.parser import Parser
from core.telegram_sender import TelegramSender
from core.tools import log_error
from core.raw_plotter import RawPlotConfig


CONFIG_PATH = 'config.ini'
PLOT_CONFIG_PATH = 'plot_config.json'
WAIT_ON_ERROR = 60
CHECK_INTERVAL = 5


# ----------------------------------------------------------------------
# Вспомогательные функции
# ----------------------------------------------------------------------

def _is_raw_enabled(params):
    val = (params.get('raw_enabled') or 'false').lower()
    return val in ('1', 'true', 'yes', 'on')


def _get_schedule(params, global_cfg):
    """raw_mode/raw_time/raw_interval_minutes или глобальные [Schedule_Raw]."""
    mode = params.get('raw_mode') or global_cfg.get('mode', 'once')
    time_str = params.get('raw_time') or global_cfg.get('time', '')
    interval_min = int(
        params.get('raw_interval_minutes')
        or global_cfg.get('interval_minutes', 60)
    )
    return mode, time_str, interval_min


# ----------------------------------------------------------------------
# Отправка одного завода
# ----------------------------------------------------------------------

def _send_raw(section, config, sender, now, raw_plotter, raw_fields):
    """
    Скачивает сырые данные по секции, строит график, шлёт фото + CSV.
    raw_plotter и raw_fields — глобальные, читаются один раз в raw_process_main.
    """
    params = dict(config.items(section))
    name = params.get('msg', section)

    try:
        parser = Parser(
            mill_uuid=params.get('milluuid'),
            host=params.get('host'),
            port=params.get('port'),
            from_minutes=int(params.get('raw_from_minutes', '-10')),
            to_minutes=int(params.get('raw_to_minutes', '0')),
            raw_fields=raw_fields,
        )

        if not parser.ping():
            sender.send_message(f"⚠️ Мельница {name} сейчас недоступна (raw)")
            return

        df = parser.fetch_raw_data()

        from_str = parser.from_local.strftime('%Y-%m-%d %H:%M')
        to_str = parser.to_local.strftime('%Y-%m-%d %H:%M')
        time_range = f"[{from_str} - {to_str}]"

        if df.empty:
            sender.send_message(f"Сырые данные для {name} за {time_range} пусты")
            return

        # --- график ---
        saved_path = None
        try:
            saved_path = raw_plotter.build_raw_plot(df, section)
        except Exception as e:
            log_error(f"Ошибка построения raw-графика для {section}: {e}")

        if saved_path:
            sender.send_photo(saved_path, caption=f"RAW {time_range} {name}")

        # --- CSV ---
        csv_bytes = parser.get_raw_data_csv_bytes()
        csv_name = parser.get_raw_data_csv_filename()

        sender.send_document_bytes(
            csv_bytes,
            filename=f"{section}__{csv_name}",
            caption=f"CSV RAW {time_range} {name}",
        )
    except Exception as e:
        log_error(f"Ошибка в _send_raw для {section}: {e}")
        raise


# ----------------------------------------------------------------------
# Обработка расписания одной секции
# ----------------------------------------------------------------------

def _process_section(section, mode, time_str, interval_min,
                     last_sent, now, today, now_time,
                     config, sender, raw_plotter, raw_fields):
    display_name = dict(config.items(section)).get('msg', section) + " (raw)"

    def send():
        _send_raw(section, config, sender, now, raw_plotter, raw_fields)

    if mode == 'once':
        if section not in last_sent or last_sent[section].get('type') != 'once':
            try:
                send()
                last_sent[section] = {'type': 'once', 'sent': True}
            except Exception as e:
                log_error(f"Ошибка отправки {display_name} (once): {e}")
        return

    if mode == 'interval':
        last_entry = last_sent.get(section)
        if last_entry is None or last_entry.get('type') != 'interval':
            try:
                send()
                last_sent[section] = {'type': 'interval', 'last_time': now}
            except Exception as e:
                log_error(f"Ошибка отправки {display_name} (interval, first): {e}")
        else:
            elapsed = (now - last_entry['last_time']).total_seconds()
            if elapsed >= interval_min * 60:
                try:
                    send()
                    last_sent[section]['last_time'] = now
                except Exception as e:
                    log_error(f"Ошибка отправки {display_name} (interval): {e}")
        return

    if mode == 'daily':
        times = [t.strip() for t in time_str.split(',') if t.strip()]
        if not times:
            return

        valid_times = []
        for t in times:
            try:
                valid_times.append(datetime.strptime(t, '%H:%M').time())
            except Exception:
                log_error(f"Некорректное время raw-таймера: {t}")
        if not valid_times:
            return

        if (section not in last_sent
                or last_sent[section].get('type') != 'daily'
                or last_sent[section].get('date') != today):
            last_sent[section] = {'type': 'daily', 'date': today, 'sent_times': set()}

        sent_times = last_sent[section]['sent_times']

        for t in valid_times:
            time_key = t.strftime('%H:%M')
            if time_key in sent_times:
                continue
            # "уже наступило" — догоняем слот, даже если цикл задержался
            if (now_time.hour, now_time.minute) >= (t.hour, t.minute):
                try:
                    send()
                    sent_times.add(time_key)
                except Exception as e:
                    log_error(f"Ошибка отправки {display_name} в {time_key}: {e}")
        return


# ----------------------------------------------------------------------
# Точка входа процесса
# ----------------------------------------------------------------------

def raw_process_main():
    """Свой независимый цикл. Работает в отдельном процессе."""
    last_sent = {}

    while True:
        try:
            config = configparser.ConfigParser(interpolation=None)
            config.read(CONFIG_PATH, encoding='utf-8')

            global_cfg = {
                'mode': config.get('Schedule_Raw', 'mode', fallback='once'),
                'time': config.get('Schedule_Raw', 'time', fallback=''),
                'interval_minutes': config.get(
                    'Schedule_Raw', 'interval_minutes', fallback='60'
                ),
            }

            token = config.get('Telegram', 'token')
            chat_id = config.get('Telegram', 'chat_id')
            sender = TelegramSender(token, chat_id)

            # --- глобальные raw-настройки из plot_config.json ---
            try:
                raw_plotter = RawPlotConfig()
                raw_fields = raw_plotter.get_raw_fields()
            except Exception as e:
                log_error(f"Ошибка чтения {PLOT_CONFIG_PATH}: {e}")
                time.sleep(WAIT_ON_ERROR)
                continue

            if not raw_fields:
                log_error(f"{PLOT_CONFIG_PATH}: пустой список полей")
                time.sleep(WAIT_ON_ERROR)
                continue
            # ------------------------------------------------------

            factory_sections = [s for s in config.sections() if s.startswith('Factory_')]
            if not factory_sections:
                time.sleep(CHECK_INTERVAL)
                continue

            now = datetime.now().astimezone()
            today = now.date()
            now_time = now.time()

            for section in factory_sections:
                params = dict(config.items(section))
                if not _is_raw_enabled(params):
                    continue

                mode, time_str, interval_min = _get_schedule(params, global_cfg)

                _process_section(
                    section=section,
                    mode=mode,
                    time_str=time_str,
                    interval_min=interval_min,
                    last_sent=last_sent,
                    now=now,
                    today=today,
                    now_time=now_time,
                    config=config,
                    sender=sender,
                    raw_plotter=raw_plotter,
                    raw_fields=raw_fields,
                )

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log_error(f"Критическая ошибка в raw-процессе: {e}")
            time.sleep(WAIT_ON_ERROR)