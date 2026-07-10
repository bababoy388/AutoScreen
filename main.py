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
    print(f"[DEBUG] send_subplot: начат для {subplot_section}")
    subplot_params = dict(config.items(subplot_section))
    mill_uuid = subplot_params.get('milluuid')
    host = subplot_params.get('host')
    port = subplot_params.get('port')
    from_min = int(subplot_params.get('from_minutes', '-720'))
    to_min = int(subplot_params.get('to_minutes', '0'))

    print(f"[DEBUG] mill_uuid={mill_uuid}, host={host}, port={port}, from={from_min}, to={to_min}")

    try:
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
            print(f"[DEBUG] Нет данных, отправляем сообщение: {msg_text}")
            sender.send_message(msg_text)
            return

        print(f"[DEBUG] DataFrame получен, строк: {len(df)}")
        saved_path = plotter.build_subplot(subplot_section, df)
        if saved_path:
            caption = subplot_params.get('msg', subplot_section)
            print(f"[DEBUG] График сохранён: {saved_path}, отправляем фото с подписью: {caption}")
            sender.send_photo(saved_path, caption=caption)
        else:
            print("[DEBUG] build_subplot вернул None")
    except Exception as e:
        print(f"[ERROR] Ошибка в send_subplot: {e}")
        log_error(f"Ошибка в send_subplot: {e}")
        raise


def worker_loop():
    WAIT_ON_ERROR = 60
    CHECK_INTERVAL = 5
    last_sent = {}  # {subplot: {'date': date, 'time': time_str}}

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
            print(f"\n[DEBUG] ====== Цикл в {now.strftime('%Y-%m-%d %H:%M:%S')} ======")
            print(f"[DEBUG] Глобальные настройки: mode={global_schedule['mode']}, time={global_schedule['time']}, interval={global_schedule['interval_minutes']}")

            for subplot_section in subplot_sections:
                subplot_params = dict(config.items(subplot_section))
                mode, time_str, interval_min = get_subplot_schedule(subplot_params, global_schedule)
                print(f"[DEBUG] {subplot_section}: mode={mode}, time_str='{time_str}', interval_min={interval_min}")

                if mode == 'once':
                    if subplot_section not in last_sent:
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = {'date': now.date(), 'time': time_str}
                            print(f"[DEBUG] {subplot_section} отправлен (once)")
                        except Exception as e:
                            print(f"[ERROR] Ошибка отправки {subplot_section}: {e}")
                            log_error(f"Ошибка отправки {subplot_section}: {e}")
                    else:
                        print(f"[DEBUG] {subplot_section} уже отправлен в режиме once, пропускаем")
                    continue

                elif mode == 'interval':
                    last = last_sent.get(subplot_section)
                    if last is None:
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = {'date': now, 'time': time_str}
                            print(f"[DEBUG] {subplot_section} отправлен (интервал, первый раз)")
                        except Exception as e:
                            print(f"[ERROR] Ошибка отправки {subplot_section}: {e}")
                            log_error(f"Ошибка отправки {subplot_section}: {e}")
                    else:
                        last_time = last['date']
                        elapsed = (now - last_time).total_seconds()
                        if elapsed >= interval_min * 60:
                            try:
                                send_subplot(subplot_section, config, sender, plotter, now)
                                last_sent[subplot_section] = {'date': now, 'time': time_str}
                                print(f"[DEBUG] {subplot_section} отправлен (интервал)")
                            except Exception as e:
                                print(f"[ERROR] Ошибка отправки {subplot_section}: {e}")
                                log_error(f"Ошибка отправки {subplot_section}: {e}")
                        else:
                            print(f"[DEBUG] Интервал: до следующей отправки {subplot_section} осталось {interval_min*60 - elapsed:.1f}с")
                    continue

                elif mode == 'daily':
                    times = [t.strip() for t in time_str.split(',') if t.strip()]
                    if not times:
                        print(f"[WARN] Для {subplot_section} не задано время в daily режиме, пропускаем")
                        continue

                    valid_times = []
                    for t in times:
                        try:
                            time_obj = datetime.strptime(t, '%H:%M').time()
                            valid_times.append(time_obj)
                        except Exception as e:
                            print(f"[WARN] Неверный формат времени '{t}': {e}")
                    if not valid_times:
                        print(f"[WARN] Нет валидных времён для {subplot_section}, пропускаем")
                        continue

                    today = now.date()
                    last_entry = last_sent.get(subplot_section)

                    # Проверяем, отправляли ли сегодня с таким же временем
                    if last_entry and last_entry['date'] == today and last_entry['time'] == time_str:
                        print(f"[DEBUG] {subplot_section} уже отправлен сегодня ({today}) в {time_str}, пропускаем")
                        continue

                    # Если время изменилось или отправки не было – проверяем, пора ли отправлять
                    now_time = now.time()
                    should_send = any(t <= now_time for t in valid_times)

                    if should_send:
                        print(f"[DEBUG] {subplot_section}: время наступило (одно из {', '.join([t.strftime('%H:%M') for t in valid_times])}), отправляем сейчас")
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = {'date': today, 'time': time_str}
                            print(f"[DEBUG] {subplot_section} отправлен (daily)")
                        except Exception as e:
                            print(f"[ERROR] Ошибка отправки {subplot_section}: {e}")
                            log_error(f"Ошибка отправки {subplot_section}: {e}")
                            # Если ошибка, не обновляем last_sent, чтобы попробовать снова
                    else:
                        # Вычисляем ближайшее будущее время для информативности
                        future_times = [t for t in valid_times if t > now_time]
                        if future_times:
                            next_time = min(future_times)
                            target = now.replace(hour=next_time.hour, minute=next_time.minute, second=0, microsecond=0)
                            wait = (target - now).total_seconds()
                            print(f"[DEBUG] {subplot_section}: ближайшее время сегодня {next_time.strftime('%H:%M')}, wait={wait:.1f}с")
                        else:
                            # Все времена прошли сегодня, но отправки не было – отправляем сейчас (раз уж пропустили)
                            print(f"[DEBUG] {subplot_section}: все времена прошли сегодня, отправляем сейчас")
                            try:
                                send_subplot(subplot_section, config, sender, plotter, now)
                                last_sent[subplot_section] = {'date': today, 'time': time_str}
                                print(f"[DEBUG] {subplot_section} отправлен (daily, пропущенное время)")
                            except Exception as e:
                                print(f"[ERROR] Ошибка отправки {subplot_section}: {e}")
                                log_error(f"Ошибка отправки {subplot_section}: {e}")
                    continue

            # Спим до следующей проверки (каждые 5 секунд)
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[CRITICAL] Критическая ошибка в рабочем цикле: {e}")
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