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
    last_sent = {}

    while True:
        try:
            # 1. Читаем конфиг
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

            # 2. Обрабатываем каждый сабплот
            for subplot_section in subplot_sections:
                subplot_params = dict(config.items(subplot_section))
                mode, time_str, interval_min = get_subplot_schedule(subplot_params, global_schedule)
                print(f"[DEBUG] {subplot_section}: mode={mode}, time_str='{time_str}', interval_min={interval_min}")

                if mode == 'once':
                    if subplot_section not in last_sent:
                        print(f"[DEBUG] Режим once: отправка для {subplot_section}")
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = True
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
                        print(f"[DEBUG] Интервал: первая отправка для {subplot_section}")
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = now
                            print(f"[DEBUG] {subplot_section} отправлен (интервал, первый раз)")
                        except Exception as e:
                            print(f"[ERROR] Ошибка отправки {subplot_section}: {e}")
                            log_error(f"Ошибка отправки {subplot_section}: {e}")
                    else:
                        elapsed = (now - last).total_seconds()
                        if elapsed >= interval_min * 60:
                            print(f"[DEBUG] Интервал: прошло {elapsed:.1f}с >= {interval_min*60}с, отправляем {subplot_section}")
                            try:
                                send_subplot(subplot_section, config, sender, plotter, now)
                                last_sent[subplot_section] = now
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
                    last_date = last_sent.get(subplot_section)

                    # Если уже отправляли сегодня – пропускаем до завтра
                    if last_date == today:
                        print(f"[DEBUG] {subplot_section} уже отправлен сегодня ({today}), пропускаем")
                        continue

                    # Не отправляли сегодня – проверяем, есть ли время, которое уже наступило или наступает сейчас (в пределах 5 секунд)
                    now_time = now.time()
                    # Ищем время, которое должно наступить в ближайшие 5 секунд (или уже прошло)
                    # Для простоты будем считать, что если текущее время >= заданному (с учётом того, что мы проверяем раз в 5 секунд),
                    # то отправляем. Но чтобы не отправлять каждый цикл, после отправки ставим last_sent = today.
                    # Проверяем все времена: если любое из них <= now_time, то считаем, что пора.
                    # Однако нужно учесть, что если время уже прошло, но мы ещё не отправляли сегодня, то нужно отправить один раз.
                    # Сделаем так: если какое-то время <= now_time, отправляем сейчас (если ещё не отправляли сегодня).
                    # Это сработает даже если время было пропущено (например, бот был выключен).
                    should_send = any(t <= now_time for t in valid_times)
                    if should_send:
                        print(f"[DEBUG] {subplot_section}: время наступило (одно из {', '.join([t.strftime('%H:%M') for t in valid_times])}), отправляем сейчас")
                        try:
                            send_subplot(subplot_section, config, sender, plotter, now)
                            last_sent[subplot_section] = today
                            print(f"[DEBUG] {subplot_section} отправлен (daily)")
                        except Exception as e:
                            print(f"[ERROR] Ошибка отправки {subplot_section}: {e}")
                            log_error(f"Ошибка отправки {subplot_section}: {e}")
                            # Если ошибка, не обновляем last_sent, чтобы попробовать снова в следующем цикле
                    else:
                        # Вычисляем ближайшее будущее время для информативности
                        future_times = [t for t in valid_times if t > now_time]
                        if future_times:
                            next_time = min(future_times)
                            target = now.replace(hour=next_time.hour, minute=next_time.minute, second=0, microsecond=0)
                            wait = (target - now).total_seconds()
                            print(f"[DEBUG] {subplot_section}: ближайшее время сегодня {next_time.strftime('%H:%M')}, wait={wait:.1f}с")
                        else:
                            # Все времена уже прошли сегодня, но мы ещё не отправляли (это странно, но возможно при первом запуске)
                            # Тогда отправляем сейчас, раз уж пропустили
                            print(f"[DEBUG] {subplot_section}: все времена прошли сегодня, но отправки не было – отправляем сейчас")
                            try:
                                send_subplot(subplot_section, config, sender, plotter, now)
                                last_sent[subplot_section] = today
                                print(f"[DEBUG] {subplot_section} отправлен (daily, пропущенное время)")
                            except Exception as e:
                                print(f"[ERROR] Ошибка отправки {subplot_section}: {e}")
                                log_error(f"Ошибка отправки {subplot_section}: {e}")
                    continue

            # 3. Спим до следующей проверки
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