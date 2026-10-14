import io
import socket
import zipfile
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from core.tools import retry_request, log_error


class Parser:
    def __init__(self, mill_uuid, host, port, from_minutes, to_minutes):
        self.mill_uuid = mill_uuid
        self.host = host
        self.port = port
        self.from_time, self.to_time, self.from_local, self.to_local = \
            self._compute_time_range(from_minutes, to_minutes)

        self._zip_bytes = None
        self._csv_bytes = None
        self._csv_filename = None
        self._zip_filename = None

    def ping(self, timeout=15):
        """Проверяет доступность хоста и порта. Возвращает True, если соединение установлено."""
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

    def _get_zip_filename(self):
        url = f"http://{self.host}:{self.port}/api/ProcessedData/csv"
        params = {
            "millUuid": self.mill_uuid,
            "from": self.from_time,
            "to": self.to_time
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
        if not self.mill_uuid:
            raise ValueError("millUuid не может быть пустым для скачивания файла")

        encoded_name = requests.utils.quote(file_name, safe='')
        url = f"http://{self.host}:{self.port}/api/RawData/download"
        params = {"fileName": encoded_name, "millUuid": self.mill_uuid}
        try:
            resp = retry_request(lambda: requests.get(url, params=params, timeout=600))
            resp.raise_for_status()
            self._zip_bytes = io.BytesIO(resp.content)
            self._zip_bytes.seek(0)
            self._zip_filename = file_name
            return self._zip_bytes
        except Exception as e:
            log_error(f"Ошибка в _download_zip_bytes для файла {file_name}: {e}")
            raise

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

    def get_raw_zip_bytes(self) -> bytes:
        """Оригинальный ZIP-файл с сервера."""
        if self._zip_bytes is None:
            raise RuntimeError("ZIP ещё не скачан. Вызовите get_dataframe().")
        return self._zip_bytes.getvalue()

    def get_raw_csv_bytes(self) -> bytes:
        """Оригинальный CSV из ZIP, без пересборки через pandas."""
        if self._csv_bytes is None:
            raise RuntimeError("CSV ещё не извлечён. Вызовите get_dataframe().")
        return self._csv_bytes

    def get_raw_csv_filename(self) -> str:
        return self._csv_filename or "data.csv"

    def get_raw_zip_filename(self) -> str:
        return self._zip_filename or "data.zip"