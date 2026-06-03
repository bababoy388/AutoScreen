import configparser
import time
from datetime import datetime, timedelta
from core.parser import Parser
from core.builder_graph import PlotConfig
from core.telegram_sender import TelegramSender
from core.tools import log_error


def main():
    config = configparser.ConfigParser(interpolation=None)
    config.read('config.ini', encoding='utf-8')
    plotter = PlotConfig('config.ini')

    sender = None
    token = config.get('Telegram', 'token')
    chat_id = config.get('Telegram', 'chat_id')
    proxy_raw = config.get('Telegram', 'proxy', fallback=None)
    proxy = proxy_raw.strip() if proxy_raw else None
    sender = TelegramSender(token, chat_id, proxy)

    mode = config.get('Schedule', 'mode', fallback='once')

    sample_section = None
    for s in config.sections():
        if s.startswith('Plot_'):
            sample_section = s
            break

    if not sample_section:
        log_error("Не найдено ни одной секции Plot_ в конфиге")
        return

    WAIT_ON_ERROR = 60

    while True:
        try:
            iteration_start = time.monotonic()

            parser = Parser(
                mill_uuid=config.get(sample_section, 'millUuid'),
                from_minutes=config.getint(sample_section, 'from_minutes'),
                to_minutes=config.getint(sample_section, 'to_minutes'),
                host_info=config.get(sample_section, 'info_host'),
                port_info=config.get(sample_section, 'info_port'),
                host_download=config.get(sample_section, 'download_host'),
                port_download=config.get(sample_section, 'download_port')
            )

            df = parser.get_dataframe()

            if df.empty:
                msg_text = f"Нет данных за период [{parser.from_time} — {parser.to_time}]"
                if sender:
                    try:
                        sender.send_message(msg_text)
                    except Exception as e:
                        log_error(f"Не удалось отправить сообщение о пустом DataFrame: {e}")
                else:
                    print(msg_text)
            else:
                for section in config.sections():
                    if section.startswith('Plot_'):
                        if config.getboolean(section, 'upload', fallback=True):
                            saved_path = plotter.build_for_section(section, df)
                            if saved_path and sender:
                                time_range = f"[{parser.from_time} — {parser.to_time}]"
                                msg = config.get(section, 'msg', fallback='').strip()
                                caption = f"{time_range} {msg}" if msg else time_range
                                sender.send_photo(saved_path, caption=caption)
                        else:
                            pass

                for section in config.sections():
                    if section.startswith('Subplot_'):
                        saved_path = plotter.build_subplot(section, df)
                        if saved_path and sender:
                            time_range = f"[{parser.from_time} — {parser.to_time}]"
                            msg = config.get(section, 'msg', fallback='').strip()
                            caption = f"{time_range} {msg}" if msg else time_range
                            sender.send_photo(saved_path, caption=caption)

            if mode == 'once':
                break
            elif mode == 'daily':
                time_str = config.get('Schedule', 'time', fallback='00:00')
                target_time = datetime.strptime(time_str, '%H:%M').time()
                now = datetime.now().astimezone()
                target_dt = now.replace(hour=target_time.hour, minute=target_time.minute,
                                        second=0, microsecond=0)
                if target_dt <= now:
                    target_dt += timedelta(days=1)
                wait_seconds = (target_dt - datetime.now().astimezone()).total_seconds()
            elif mode == 'interval':
                interval_min = config.getint('Schedule', 'interval_minutes', fallback=60)
                desired_wait = interval_min * 60
                elapsed = time.monotonic() - iteration_start
                wait_seconds = max(0, desired_wait - elapsed)
            else:
                break

            time.sleep(wait_seconds)

        except KeyboardInterrupt:
            print("\nРабота остановлена пользователем.")
            break
        except Exception as e:
            log_error(f"Критическая ошибка в основном цикле: {e}")
            time.sleep(WAIT_ON_ERROR)
            if mode == 'once':
                break


if __name__ == "__main__":
    main()