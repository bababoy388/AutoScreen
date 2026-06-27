import configparser
import time
import threading
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from core.parser import Parser
from core.builder_graph import PlotConfig
from core.telegram_sender import TelegramSender
from core.tools import log_error
from core.bot_polling import dp, bot


def worker_loop():
    WAIT_ON_ERROR = 60
    CHECK_INTERVAL = 5

    while True:
        try:
            # --- 1. Читаем конфиг ---
            config = configparser.ConfigParser(interpolation=None)
            config.read('config.ini', encoding='utf-8')

            # --- 2. Инициализация отправителя ---
            token = config.get('Telegram', 'token')
            chat_id = config.get('Telegram', 'chat_id')
            proxy_raw = config.get('Telegram', 'proxy', fallback=None)
            proxy = proxy_raw.strip() if proxy_raw else None
            sender = TelegramSender(token, chat_id, proxy)

            # --- 3. Режим работы ---
            mode = config.get('Schedule', 'mode', fallback='once')
            time_str = config.get('Schedule', 'time', fallback='00:00')
            interval_min = config.getint('Schedule', 'interval_minutes', fallback=60)

            # --- 4. Определяем время до следующего события ---
            if mode == 'once':
                pass
            elif mode == 'daily':
                target_time = datetime.strptime(time_str, '%H:%M').time()
                now = datetime.now().astimezone()
                target_dt = now.replace(hour=target_time.hour, minute=target_time.minute,
                                        second=0, microsecond=0)
                if target_dt <= now:
                    target_dt += timedelta(days=1)
                wait_seconds = (target_dt - datetime.now().astimezone()).total_seconds()
            elif mode == 'interval':
                wait_seconds = interval_min * 60
            else:
                break

            # --- 5. Ожидание с проверкой изменений конфига ---
            # Если режим 'once', то не ждём, а сразу отправляем
            if mode == 'once':
                pass  # пропускаем ожидание
            else:
                remaining = wait_seconds
                config_changed = False
                while remaining > 0:
                    sleep_time = min(CHECK_INTERVAL, remaining)
                    time.sleep(sleep_time)
                    remaining -= sleep_time

                    # Проверяем изменения конфига
                    temp_config = configparser.ConfigParser(interpolation=None)
                    temp_config.read('config.ini', encoding='utf-8')
                    new_mode = temp_config.get('Schedule', 'mode', fallback='once')
                    if new_mode != mode:
                        config_changed = True
                        break
                    if mode == 'interval':
                        new_interval = temp_config.getint('Schedule', 'interval_minutes', fallback=60)
                        if new_interval != interval_min:
                            config_changed = True
                            break
                    if mode == 'daily':
                        new_time = temp_config.get('Schedule', 'time', fallback='00:00')
                        if new_time != time_str:
                            config_changed = True
                            break

                if config_changed:
                    continue

            # --- Группировка секций ---
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

            plotter = PlotConfig('config.ini')
            for key, sections in groups.items():
                mill_uuid, info_host, info_port, download_host, download_port, from_min, to_min = key

                try:
                    parser = Parser(
                        mill_uuid=mill_uuid,
                        from_minutes=from_min,
                        to_minutes=to_min,
                        host_info=info_host,
                        port_info=info_port,
                        host_download=download_host,
                        port_download=download_port
                    )

                    df = parser.get_dataframe()
                    first_section = sections[0]
                    mill_name = config.get(first_section, 'msg', fallback=mill_uuid).strip() or mill_uuid

                    if df.empty:
                        from_local_str = parser.from_local.strftime('%Y-%m-%d %H:%M')
                        to_local_str = parser.to_local.strftime('%Y-%m-%d %H:%M')
                        time_range = f"[{from_local_str} - {to_local_str}]"
                        msg_text = f"Нет данных для {mill_name} за период {time_range}"
                        sender.send_message(msg_text)
                        continue

                    for section in sections:
                        if section.startswith('Plot_'):
                            if config.getboolean(section, 'upload', fallback=True):
                                saved_path = plotter.build_for_section(section, df)
                                if saved_path and sender:
                                    from_local_str = parser.from_local.strftime('%Y-%m-%d %H:%M')
                                    to_local_str = parser.to_local.strftime('%Y-%m-%d %H:%M')
                                    time_range = f"[{from_local_str} - {to_local_str}]"
                                    msg = config.get(section, 'msg', fallback='').strip()
                                    caption = f"{time_range} {msg}" if msg else time_range
                                    sender.send_photo(saved_path, caption=caption)
                        elif section.startswith('Subplot_'):
                            saved_path = plotter.build_subplot(section, df)
                            if saved_path and sender:
                                from_local_str = parser.from_local.strftime('%Y-%m-%d %H:%M')
                                to_local_str = parser.to_local.strftime('%Y-%m-%d %H:%M')
                                time_range = f"[{from_local_str} - {to_local_str}]"
                                msg = config.get(section, 'msg', fallback='').strip()
                                caption = f"{time_range} {msg}" if msg else time_range
                                sender.send_photo(saved_path, caption=caption)

                except Exception as e:
                    log_error(f"Ошибка при обработке группы {key}: {e}")

            # --- Если режим 'once' — выходим после отправки ---
            if mode == 'once':
                break

        except KeyboardInterrupt:
            break
        except Exception as e:
            log_error(f"Критическая ошибка в рабочем цикле: {e}")
            time.sleep(WAIT_ON_ERROR)
            try:
                if mode == 'once':
                    break
            except:
                break


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