from datetime import datetime
import time
import requests
from typing import Callable
import logging
from logging.handlers import RotatingFileHandler


logger = logging.getLogger('mill_monitor')
logger.setLevel(logging.ERROR)  # пишем только ошибки

handler = RotatingFileHandler(
    'log_errors.txt', maxBytes=1_000_000, backupCount=5, encoding='utf-8'
)
formatter = logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)

def log_error(error_message):
    logger.error(error_message)

def retry_request(
    request_func: Callable[[], requests.Response],
    max_retries=3,
    backoff_factor=1.0):

    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            response = request_func()
            if response.ok:
                return response
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"Сервер недоступен {response.status_code}", response=response)
            response.raise_for_status()
        except requests.RequestException as e:
            last_exception = e
            if attempt == max_retries:
                break
            delay = backoff_factor * (2 ** (attempt - 1))
            time.sleep(delay)
    raise last_exception