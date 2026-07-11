import configparser
import time
import threading
import asyncio
from datetime import datetime, timedelta
from core.parser import Parser
from core.builder_graph import PlotConfig
from core.telegram_sender import TelegramSender
from core.tools import log_error
from core.bot_polling import dp, bot


def get_subplot_schedule(subplot_params, global_config):
    mode = subplot_params.get('mode') or global_config.get('mode', 'once')
    time_str = subplot_params.get('time') or global_config.get('time', '')
    interval_min = int(subplot_params.get('interval_minutes') or global_config.get('interval_minutes', 60))
    return mode, time_str, interval_min


def send_subplot(subplot_section, config, sender, plotter, now):
    subplot_params = dict(config.items(subplot_section))
    mill_uuid = subplot_params.get('milluuid')
    host = subplot_params.get('host')
    port = subplot_params.get('port')
    from_min = int(subplot_params.get('from_minutes', '-720'))
    to_min = int(subplot_params.get('to_minutes', '0'))
    name = subplot_params.get('msg')


    try:
        parser = Parser(
            mill_uuid=mill_uuid,
            host=host,
            port=port,
            from_minutes=from_min,
            to_minutes=to_min
        )
        df = parser.get_dataframe()

        from_local_str = parser.from_local.strftime('%Y-%m-%d %H:%M')
        to_local_str = parser.to_local.strftime('%Y-%m-%d %H:%M')
        time_range = f"[{from_local_str} - {to_local_str}]"

        if df.empty:
            msg_text = f"Нет данных для {name} за период {time_range}"
            sender.send_message(msg_text)
            return

        saved_path = plotter.build_subplot(subplot_section, df)
        if saved_path:
            caption = subplot_params.get('msg', subplot_section)
            msg = f"{time_range} {caption}"
            sender.send_photo(saved_path, caption=msg)
    except Exception as e:
        log_error(f"Ошибка в send_subplot для {subplot_section}: {e}")
        raise


def worker_loop():
    WAIT_ON_ERROR = 60
    CHECK_INTERVAL = 5
    last_sent = {}

    while True:
        try:
            config = configparser.ConfigParser(interpolation=None)
            config.read('config.ini', encoding='utf-8')

            global_schedule = {
                'mode': config.get('Schedule', 'mode', fallback='once'),
                'time': config.get('Schedule', 'time', fallback=''),
                'interval_minutes': config.get('Schedule', 'interval_minutes', fallback='60')
            }

            token = config.get('Telegram', 'token')
            chat_id = config.get('Telegram', 'chat_id')
            sender = TelegramSender(token, chat_id)

            factory_sections = [s for s in config.sections() if s.startswith('Factory_')]
            if not factory_sections:
                time.sleep(CHECK_INTERVAL)
                continue

            plotter = PlotConfig('config.ini')
            now = datetime.now().astimezone()
            today = now.date()
            now_time = now.time()

            for section in factory_sections:
                subplot_params = dict(config.items(section))
                mode, time_str, interval_min = get_subplot_schedule(subplot_params, global_schedule)
                display_name = subplot_params.get('msg', section)

                if mode == 'once':
                    if section not in last_sent or last_sent[section].get('type') != 'once':
                        try:
                            send_subplot(section, config, sender, plotter, now)
                            last_sent[section] = {'type': 'once', 'sent': True}
                        except Exception as e:
                            log_error(f"Ошибка отправки {display_name} (once): {e}")
                    continue

                elif mode == 'interval':
                    last_entry = last_sent.get(section)
                    if last_entry is None or last_entry.get('type') != 'interval':
                        try:
                            send_subplot(section, config, sender, plotter, now)
                            last_sent[section] = {'type': 'interval', 'last_time': now}
                        except Exception as e:
                            log_error(f"Ошибка отправки {display_name} (interval, first): {e}")
                    else:
                        last_time = last_entry['last_time']
                        elapsed = (now - last_time).total_seconds()
                        if elapsed >= interval_min * 60:
                            try:
                                send_subplot(section, config, sender, plotter, now)
                                last_sent[section]['last_time'] = now
                            except Exception as e:
                                log_error(f"Ошибка отправки {display_name} (interval): {e}")
                    continue

                elif mode == 'daily':
                    times = [t.strip() for t in time_str.split(',') if t.strip()]
                    if not times:
                        continue

                    valid_times = []
                    for t in times:
                        try:
                            time_obj = datetime.strptime(t, '%H:%M').time()
                            valid_times.append(time_obj)
                        except Exception:
                            print("Ошибка обработки таймера")
                    if not valid_times:
                        print("Ошибка обработки всех таймеров")
                        continue

                    if section not in last_sent or last_sent[section].get('type') != 'daily' or last_sent[section].get('date') != today:
                        last_sent[section] = {'type': 'daily', 'date': today, 'sent_times': set()}

                    sent_times = last_sent[section]['sent_times']
                    current = now_time.replace(second=0, microsecond=0)

                    for t in valid_times:
                        time_key = t.strftime('%H:%M')
                        if time_key in sent_times:
                            continue

                        if current.hour == t.hour and current.minute == t.minute:
                            try:
                                send_subplot(section, config, sender, plotter, now)
                                sent_times.add(time_key)
                                break
                            except Exception as e:
                                log_error(f"Ошибка отправки {display_name} в {time_key}: {e}")
                    continue

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log_error(f"Критическая ошибка в рабочем цикле: {e}")
            time.sleep(WAIT_ON_ERROR)


def main():
    worker_thread = threading.Thread(target=worker_loop, daemon=True)
    worker_thread.start()
    print("✅ Рабочий цикл запущен в фоновом потоке")

    try:
        asyncio.run(dp.start_polling(bot, handle_signals=False))
    except KeyboardInterrupt:
        print("\nБот остановлен пользователем.")
    except Exception as e:
        log_error(f"Ошибка в поллинге: {e}")


if __name__ == "__main__":
    main()