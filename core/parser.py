import io
import zipfile
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from core.tools import retry_request, log_error


class Parser:
    def __init__(self, mill_uuid,
                 host_info, port_info,
                 host_download, port_download,
                 from_minutes, to_minutes):
        self.mill_uuid = mill_uuid
        self.info_host = host_info
        self.info_port = port_info
        self.download_host = host_download
        self.download_port = port_download

        self.from_time, self.to_time = self._compute_time_range(from_minutes, to_minutes)

    @staticmethod
    def _compute_time_range(from_minutes, to_minutes):
        local_now = datetime.now().astimezone()
        from_dt = local_now + timedelta(minutes=from_minutes)
        to_dt = local_now + timedelta(minutes=to_minutes)
        from_utc = from_dt.astimezone(timezone.utc)
        to_utc = to_dt.astimezone(timezone.utc)

        def fmt(dt):
            return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{dt.microsecond // 1000:03d}Z"

        return fmt(from_utc), fmt(to_utc)

    def _get_zip_filename(self):
        url = f"http://{self.info_host}:{self.info_port}/api/ProcessedData/csv"
        params = {
            "millUuid": self.mill_uuid,
            "from": self.from_time,
            "to": self.to_time
        }
        try:
            resp = retry_request(lambda: requests.get(url, params=params, timeout=30))
            resp.raise_for_status()
            data = resp.json()
            return data["fileName"]
        except Exception as e:
            log_error(f"Ошибка в _get_zip_filename: {e}")
            raise

    def _download_zip_bytes(self, file_name):
        encoded_name = requests.utils.quote(file_name, safe='')
        url = (f"http://{self.download_host}:{self.download_port}"
               f"/api/RawData/download?fileName={encoded_name}")
        try:
            resp = retry_request(lambda: requests.get(url, timeout=30))
            resp.raise_for_status()
            return io.BytesIO(resp.content)
        except Exception as e:
            log_error(f"Ошибка в _download_zip_bytes для файла {file_name}: {e}")
            raise

    def _extract_csv_from_zip(self, zip_bytes):
        try:
            with zipfile.ZipFile(zip_bytes) as zf:
                csv_names = [name for name in zf.namelist()
                             if name.lower().endswith('.csv')]
                if not csv_names:
                    raise FileNotFoundError("В архиве нет CSV-файла")
                with zf.open(csv_names[0]) as csv_file:
                    return pd.read_csv(csv_file)
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