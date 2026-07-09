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
    mill_uuid = subplot_params.get('millUuid')
    host = subplot_params.get('host')
    port = subplot_params.get('port')
    from_min = int(subplot_params.get('from_minutes', '-720'))
    to_min = int(subplot_params.get('to_minutes', '0'))

    parser = Parser(
        mill_uuid=mill_uuid,
        host=host,
        port=port,
        from_minutes=from_min,
        to_minutes=to_min
    )
    df = parser.get_dataframe()
    if df.empty:
        from_local_str = parser.from_local.strftime('%Y-%m-%d %H:%M')
        to_local_str = parser.to_local.strftime('%Y-%m-%d %H:%M')
        time_range = f"[{from_local_str} - {to_local_str}]"
        msg_text = f"Нет данных для {subplot_section} за период {time_range}"
        sender.send_message(msg_text)
        return

    saved_path = plotter.build_subplot(subplot_section, df)
    if saved_path:
        caption = subplot_params.get('msg', subplot_section)
        sender.send_photo(saved_path, caption=caption)


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

            subplot_sections = [s for s in config.sections() if s.startswith('Subplot_')]
            if not subplot_sections:
                print("Нет Subplot")
                time.sleep(CHECK_INTERVAL)
                continue

            plotter = PlotConfig('config.ini')
            now = datetime.now().astimezone()

            min_wait = None
            for subplot_section in subplot_sections:
                subplot_params = dict(config.items(subplot_section))
                mode, time_str, interval_min = get_subplot_schedule(subplot_params, global_schedule)

                if mode == 'once':
                    if subplot_section not in last_sent:
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = True
                        except Exception as e:
                            log_error(f"Ошибка отправки {subplot_section}: {e}")
                    continue

                elif mode == 'interval':
                    last = last_sent.get(subplot_section)
                    if last is None:
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = now
                        except Exception as e:
                            log_error(f"Ошибка отправки {subplot_section}: {e}")
                        continue
                    else:
                        elapsed = (now - last).total_seconds()
                        if elapsed >= interval_min * 60:
                            try:
                                send_subplot(subplot_section, config, sender, plotter, now)
                                last_sent[subplot_section] = now
                            except Exception as e:
                                log_error(f"Ошибка отправки {subplot_section}: {e}")
                            continue
                        else:
                            wait = interval_min * 60 - elapsed
                            if min_wait is None or wait < min_wait:
                                min_wait = wait
                            continue

                elif mode == 'daily':
                    times = [t.strip() for t in time_str.split(',') if t.strip()]
                    valid_times = []
                    for t in times:
                        try:
                            time_obj = datetime.strptime(t, '%H:%M').time()
                            valid_times.append(time_obj)
                        except:
                            pass
                    if not valid_times:
                        continue
                    target_dts = []
                    for t in valid_times:
                        target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                        if target <= now:
                            target += timedelta(days=1)
                        target_dts.append(target)
                    next_dt = min(target_dts)
                    wait = (next_dt - now).total_seconds()
                    if wait <= 0:
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = now.date()
                        except Exception as e:
                            log_error(f"Ошибка отправки {subplot_section}: {e}")
                    else:
                        if min_wait is None or wait < min_wait:
                            min_wait = wait

            if min_wait is None:
                print("Все Subplot отработали или нет расписания, спим 5 сек...")
                time.sleep(CHECK_INTERVAL)
                continue

            remaining = min_wait
            while remaining > 0:
                sleep_time = min(CHECK_INTERVAL, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time
                break

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