import requests
from core.tools import log_error, retry_request


class TelegramSender:
    def __init__(self, token, chat_id, proxy=None):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.proxy = proxy if proxy else None

    def _get_proxies(self):
        if self.proxy:
            return {'http': self.proxy, 'https': self.proxy}
        return None

    def send_message(self, text):
        url = f"{self.base_url}/sendMessage"
        data = {'chat_id': self.chat_id, 'text': text}
        try:
            resp = retry_request(lambda: requests.post(url, data=data,
                                 proxies=self._get_proxies(), timeout=15))
            resp.raise_for_status()
        except Exception as e:
            log_error(f"Ошибка отправки сообщения: {e}")

    def send_photo(self, photo_path, caption=""):
        url = f"{self.base_url}/sendPhoto"
        with open(photo_path, 'rb') as photo_file:
            data = {'chat_id': self.chat_id, 'caption': caption}
            files = {'photo': photo_file}
            try:
                resp = retry_request(lambda: requests.post(url, data=data, files=files,
                                     proxies=self._get_proxies(), timeout=15))
                resp.raise_for_status()
            except Exception as e:
                log_error(f"Ошибка отправки фото: {e}")