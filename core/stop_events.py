"""
События остановки мельниц.

Хранилище: SQLite (events.db в корне проекта) — безопасно при доступе
из нескольких процессов (graph-worker и raw-process).

Настройки в секции Factory_* (все необязательные):
    run_column = ts                      # колонка обработанных данных — признак работы
    run_threshold = 0                    # работает, если значение > порога
    run_max_data_lag_minutes = 10        # данных нет дольше — считаем остановленной
    stop_event_tolerance_minutes = 10    # окно поиска уже зарегистрированного события
"""
import io
import os
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pandas as pd

from core.parser import Parser
from core.raw_plotter import RawPlotConfig
from core.tools import log_error


DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'events.db'
)
CONFIG_PATH = 'config.ini'
RAW_WINDOW = timedelta(minutes=2)
_TS_FMT = '%Y-%m-%dT%H:%M:%S.%f'  # всегда UTC, сравнивается как строка


# ----------------------------------------------------------------------
# Хранилище
# ----------------------------------------------------------------------

def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stop_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            factory    TEXT    NOT NULL,
            stop_time  TEXT    NOT NULL,
            source     TEXT    NOT NULL,
            created_at TEXT    NOT NULL,
            raw_sent   INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stop_factory_time "
        "ON stop_events(factory, stop_time)"
    )
    return conn


def _to_utc_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _from_utc_str(s: str) -> datetime:
    return datetime.strptime(s, _TS_FMT).replace(tzinfo=timezone.utc)


def add_event(factory: str, stop_time: datetime, source: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO stop_events (factory, stop_time, source, created_at) "
            "VALUES (?, ?, ?, ?)",
            (factory, _to_utc_str(stop_time), source,
             _to_utc_str(datetime.now(timezone.utc))),
        )
        return cur.lastrowid


def find_event_near(factory: str, stop_time: datetime, tolerance: timedelta):
    lo = _to_utc_str(stop_time - tolerance)
    hi = _to_utc_str(stop_time + tolerance)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM stop_events "
            "WHERE factory = ? AND stop_time BETWEEN ? AND ? "
            "ORDER BY stop_time DESC LIMIT 1",
            (factory, lo, hi),
        ).fetchone()
    return dict(row) if row else None


def mark_raw_sent(event_id: int):
    with _connect() as conn:
        conn.execute("UPDATE stop_events SET raw_sent = 1 WHERE id = ?", (event_id,))


# ----------------------------------------------------------------------
# Определение остановки
# ----------------------------------------------------------------------

def detect_stop(df: pd.DataFrame, params: dict, now_utc: datetime = None):
    """
    Возвращает (is_running, stop_time_utc).
      (True, None)   — мельница работает
      (False, dt)    — остановлена, dt — момент остановки
      (False, None)  — остановлена всё окно / момент не определить
      (None, None)   — недостаточно данных для вывода
    """
    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return None, None

    now_utc = now_utc or datetime.now(timezone.utc)
    max_lag = timedelta(minutes=int(params.get('run_max_data_lag_minutes', '10')))
    run_col = (params.get('run_column') or '').strip()

    idx = df.index
    last_ts = idx[-1].to_pydatetime()
    data_lagging = (now_utc - last_ts) > max_lag

    if not run_col or run_col not in df.columns:
        if run_col:
            log_error(f"run_column '{run_col}' отсутствует в обработанных данных")
        # Без признака работы можем судить только по отсутствию данных
        return (False, last_ts) if data_lagging else (None, None)

    threshold = float(params.get('run_threshold', '0'))
    values = pd.to_numeric(df[run_col], errors='coerce')
    valid = values.dropna()
    if valid.empty:
        return (False, last_ts) if data_lagging else (None, None)

    running = valid > threshold

    if bool(running.iloc[-1]):
        # Последняя точка «в работе»: если данных давно нет — остановилась после неё
        return (False, valid.index[-1].to_pydatetime()) if data_lagging else (True, None)

    true_positions = [i for i, r in enumerate(running.tolist()) if r]
    if not true_positions:
        return False, None  # стояла всё окно

    first_stopped_pos = true_positions[-1] + 1
    return False, running.index[first_stopped_pos].to_pydatetime()


# ----------------------------------------------------------------------
# Выгрузка сырых данных по событию
# ----------------------------------------------------------------------

def send_raw_for_event(section: str, params: dict, stop_time: datetime,
                       sender, event_id: int, raw_plotter: RawPlotConfig = None) -> bool:
    """Отправляет график и полный CSV сырых данных за RAW_WINDOW до stop_time."""
    name = params.get('msg', section)
    raw_plotter = raw_plotter or RawPlotConfig(CONFIG_PATH)

    to_local = stop_time.astimezone()
    from_local = to_local - RAW_WINDOW
    time_range = f"[{from_local:%Y-%m-%d %H:%M:%S} - {to_local:%Y-%m-%d %H:%M:%S}]"

    parser = Parser(
        mill_uuid=params.get('milluuid'),
        host=params.get('host'),
        port=params.get('port'),
        from_minutes=0,
        to_minutes=0,
    )
    parser.set_custom_time_range(from_local, to_local)

    if not parser.ping():
        sender.send_message(f"⚠️ {name}: событие #{event_id}, мельница недоступна для выгрузки raw")
        return False

    df = parser.fetch_raw_data()
    if df.empty:
        sender.send_message(f"⚠️ {name}: событие #{event_id}, сырые данные за {time_range} пусты")
        return False

    plot_path = f"{section}_stop_{event_id}.png"
    try:
        saved = raw_plotter.build_raw_plot(df, section, save_path=plot_path)
        if saved:
            sender.send_photo(saved, caption=f"STOP #{event_id} RAW {time_range} {name}")
    except Exception as e:
        log_error(f"Ошибка raw-графика по событию #{event_id} ({section}): {e}")
    finally:
        try:
            os.remove(plot_path)
        except OSError:
            pass

    csv_bytes = parser.get_raw_data_csv_bytes()
    csv_name = parser.get_raw_data_csv_filename()
    if len(csv_bytes) > 40 * 1024 * 1024:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr(csv_name, csv_bytes)
        csv_bytes, csv_name = buf.getvalue(), csv_name + '.zip'

    sender.send_document_bytes(
        csv_bytes,
        filename=f"{section}__stop{event_id}__{csv_name}",
        caption=f"CSV STOP #{event_id} RAW {time_range} {name}",
    )
    return True


# ----------------------------------------------------------------------
# Точка входа для выгрузки обработанных данных
# ----------------------------------------------------------------------

def check_and_handle_stop(section: str, params: dict, df: pd.DataFrame, sender):
    """
    Вызывается после получения обработанных данных.
    Если мельница стоит и события об остановке нет — создаёт его и выгружает raw.
    """
    try:
        is_running, stop_time = detect_stop(df, params)
        if is_running is not False or stop_time is None:
            return

        tolerance = timedelta(minutes=int(params.get('stop_event_tolerance_minutes', '10')))
        if find_event_near(section, stop_time, tolerance):
            return

        name = params.get('msg', section)
        event_id = add_event(section, stop_time, 'detected')
        sender.send_message(
            f"⛔ {name}: мельница не в работе, остановка ~{stop_time.astimezone():%Y-%m-%d %H:%M:%S}. "
            f"Уведомления не было — зарегистрировано событие #{event_id}, выгружаю сырые данные за 2 мин."
        )
        if send_raw_for_event(section, params, stop_time, sender, event_id):
            mark_raw_sent(event_id)
    except Exception as e:
        log_error(f"Ошибка проверки остановки для {section}: {e}")