import io
import socket
import zipfile
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from core.tools import retry_request, log_error


DEFAULT_RAW_FIELDS = [
    "v0_1", "v0_2", "v0_3",
    "v1_1", "v1_2", "v1_3",
    "v2_1", "v2_2", "v2_3", "v2_4", "v2_5", "v2_6", "v2_7", "v2_8",
]


class Parser:
    def __init__(self, mill_uuid, host, port, from_minutes, to_minutes,
                 raw_fields=None):
        self.mill_uuid = mill_uuid
        self.host = host
        self.port = port
        self.from_time, self.to_time, self.from_local, self.to_local = \
            self._compute_time_range(from_minutes, to_minutes)

        # Поля для сырых данных. Можно переопределить в конструкторе.
        self.raw_fields = list(raw_fields) if raw_fields else list(DEFAULT_RAW_FIELDS)

        # --- кэш обработанных данных (ProcessedData) ---
        self._zip_bytes = None
        self._csv_bytes = None
        self._csv_filename = None
        self._zip_filename = None

        # --- кэш сырых данных (RawData) ---
        self._raw_zip_bytes = None
        self._raw_csv_bytes = None
        self._raw_csv_filename = None
        self._raw_zip_filename = None

    # ------------------------------------------------------------------
    # Общие утилиты
    # ------------------------------------------------------------------

    def ping(self, timeout=15):
        try:
            with socket.create_connection((self.host, int(self.port)), timeout=timeout):
                return True
        except (socket.timeout, socket.error, ValueError):
            return False

    def get_pretty_time_range(self):
        return (self.from_local.strftime('%Y-%m-%d %H:%M'),
                self.to_local.strftime('%Y-%m-%d %H:%M'))

    def set_custom_time_range(self, from_dt: datetime, to_dt: datetime):
        def fmt(dt):
            dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.strftime('%Y-%m-%dT%H:%M:%S.') + f"{dt_utc.microsecond // 1000:03d}Z"

        self.from_time = fmt(from_dt)
        self.to_time = fmt(to_dt)
        self.from_local = from_dt
        self.to_local = to_dt

    def set_raw_fields(self, fields):
        """Переопределить список полей для RawData."""
        self.raw_fields = list(fields)

    @staticmethod
    def _compute_time_range(from_minutes, to_minutes):
        local_now = datetime.now().astimezone()
        from_dt = local_now + timedelta(minutes=from_minutes)
        to_dt = local_now + timedelta(minutes=to_minutes)

        from_local = from_dt
        to_local = to_dt

        from_utc = from_dt.astimezone(timezone.utc)
        to_utc = to_dt.astimezone(timezone.utc)

        def fmt(dt):
            return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{dt.microsecond // 1000:03d}Z"

        return fmt(from_utc), fmt(to_utc), from_local, to_local

    def _download_zip(self, file_name):
        """Скачивает ZIP с /api/RawData/download (общий эндпоинт для обоих типов)."""
        if not self.mill_uuid:
            raise ValueError("millUuid не может быть пустым для скачивания файла")

        encoded_name = requests.utils.quote(file_name, safe='')
        url = f"http://{self.host}:{self.port}/api/RawData/download"
        params = {"fileName": encoded_name, "millUuid": self.mill_uuid}
        try:
            resp = retry_request(lambda: requests.get(url, params=params, timeout=600))
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            log_error(f"Ошибка скачивания ZIP {file_name}: {e}")
            raise

    # ------------------------------------------------------------------
    # ProcessedData (для графика)
    # ------------------------------------------------------------------

    def _get_zip_filename(self):
        url = f"http://{self.host}:{self.port}/api/ProcessedData/csv"
        params = {
            "millUuid": self.mill_uuid,
            "from": self.from_time,
            "to": self.to_time,
        }
        try:
            resp = retry_request(lambda: requests.get(url, params=params, timeout=600))
            resp.raise_for_status()
            data = resp.json()
            return data["fileName"]
        except Exception as e:
            log_error(f"Ошибка в _get_zip_filename: {e}")
            raise

    def _download_zip_bytes(self, file_name):
        content = self._download_zip(file_name)
        self._zip_bytes = io.BytesIO(content)
        self._zip_bytes.seek(0)
        self._zip_filename = file_name
        return self._zip_bytes

    def _extract_csv_from_zip(self, zip_bytes):
        try:
            zip_bytes.seek(0)
            with zipfile.ZipFile(zip_bytes) as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith('.csv')]
                if not csv_names:
                    raise FileNotFoundError("В архиве нет CSV-файла")
                self._csv_filename = csv_names[0]
                self._csv_bytes = zf.read(self._csv_filename)
            zip_bytes.seek(0)
            return pd.read_csv(io.BytesIO(self._csv_bytes))
        except Exception as e:
            log_error(f"Ошибка в _extract_csv_from_zip: {e}")
            raise

    def _prepare_dataframe(self, df):
        df = df.copy()
        df['time'] = pd.to_datetime(df['time'], utc=True)
        df.set_index('time', inplace=True)
        df.index.name = None
        return df

    def get_dataframe(self):
        file_name = self._get_zip_filename()
        zip_bytes = self._download_zip_bytes(file_name)
        df = self._extract_csv_from_zip(zip_bytes)
        df = self._prepare_dataframe(df)
        return df

    # --- геттеры "сырых" processed-данных (оригинал из ZIP) ---

    def get_raw_zip_bytes(self) -> bytes:
        if self._zip_bytes is None:
            raise RuntimeError("Processed ZIP ещё не скачан. Вызовите get_dataframe().")
        return self._zip_bytes.getvalue()

    def get_raw_csv_bytes(self) -> bytes:
        if self._csv_bytes is None:
            raise RuntimeError("Processed CSV ещё не извлечён. Вызовите get_dataframe().")
        return self._csv_bytes

    def get_raw_csv_filename(self) -> str:
        return self._csv_filename or "processed.csv"

    def get_raw_zip_filename(self) -> str:
        return self._zip_filename or "processed.zip"

    # ------------------------------------------------------------------
    # RawData (сырые данные)
    # ------------------------------------------------------------------

    def _get_raw_zip_filename(self):
        url = f"http://{self.host}:{self.port}/api/RawData/csv"
        params = {
            "millUuid": self.mill_uuid,
            "from": self.from_time,
            "to": self.to_time,
        }
        if self.raw_fields:
            params["fields"] = ",".join(self.raw_fields)
        try:
            resp = retry_request(lambda: requests.get(url, params=params, timeout=600))
            resp.raise_for_status()
            data = resp.json()
            return data["fileName"]
        except Exception as e:
            log_error(f"Ошибка в _get_raw_zip_filename: {e}")
            raise

    def _download_raw_zip_bytes(self, file_name):
        content = self._download_zip(file_name)
        self._raw_zip_bytes = io.BytesIO(content)
        self._raw_zip_bytes.seek(0)
        self._raw_zip_filename = file_name
        return self._raw_zip_bytes

    def _extract_raw_csv_from_zip(self, zip_bytes):
        try:
            zip_bytes.seek(0)
            with zipfile.ZipFile(zip_bytes) as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith('.csv')]
                if not csv_names:
                    raise FileNotFoundError("В архиве нет CSV-файла")
                self._raw_csv_filename = csv_names[0]
                self._raw_csv_bytes = zf.read(self._raw_csv_filename)
            zip_bytes.seek(0)
            return pd.read_csv(io.BytesIO(self._raw_csv_bytes))
        except Exception as e:
            log_error(f"Ошибка в _extract_raw_csv_from_zip: {e}")
            raise

    def fetch_raw_data(self):
        """
        Скачивает сырые данные через /api/RawData/csv с полями self.raw_fields.
        Возвращает pandas.DataFrame, а также наполняет кэш:
          - get_raw_data_zip_bytes()
          - get_raw_data_csv_bytes()
          - get_raw_data_csv_filename()
          - get_raw_data_zip_filename()
        """
        file_name = self._get_raw_zip_filename()
        zip_bytes = self._download_raw_zip_bytes(file_name)
        df = self._extract_raw_csv_from_zip(zip_bytes)
        return df

    # --- геттеры сырых RawData ---

    def get_raw_data_zip_bytes(self) -> bytes:
        if self._raw_zip_bytes is None:
            raise RuntimeError("RawData ZIP ещё не скачан. Вызовите fetch_raw_data().")
        return self._raw_zip_bytes.getvalue()

    def get_raw_data_csv_bytes(self) -> bytes:
        if self._raw_csv_bytes is None:
            raise RuntimeError("RawData CSV ещё не извлечён. Вызовите fetch_raw_data().")
        return self._raw_csv_bytes

    def get_raw_data_csv_filename(self) -> str:
        return self._raw_csv_filename or "raw.csv"

    def get_raw_data_zip_filename(self) -> str:
        return self._raw_zip_filename or "raw.zip"