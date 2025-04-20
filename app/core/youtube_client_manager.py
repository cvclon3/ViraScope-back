# app/core/youtube_client_manager.py
import random
import logging
from typing import List, Optional, Dict
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import settings # Импортируем settings

logger = logging.getLogger(__name__)

YOUTUBE_API_SERVICE_NAME = 'youtube'
YOUTUBE_API_VERSION = 'v3'

class ApiKeyManager:
    def __init__(self):
        self.keys: List[str] = []
        if settings.youtube_api_keys:
            self.keys = [key.strip() for key in settings.youtube_api_keys.split(',') if key.strip()]
        else:
            logger.warning("No YouTube API keys found in settings (YOUTUBE_API_KEYS). Search functionality will likely fail.")

        if not self.keys:
             logger.error("API Key list is empty. Cannot create YouTube clients.")
             # Можно добавить обработку ошибки или оставить пустым, тогда get_client будет возвращать None

        self.current_key_index: int = 0
        # Словарь для хранения временно истощенных ключей {index: exhausted_utc_datetime}
        self.exhausted_keys: Dict[int, datetime] = {}
        self._last_used_index: Optional[int] = None # Индекс ключа, который был выдан последним

    def _is_key_valid(self, index: int) -> bool:
        """Проверяет, не истощен ли ключ на сегодня."""
        if index not in self.exhausted_keys:
            return True # Не помечен как истощенный

        exhausted_time = self.exhausted_keys[index]
        now_utc = datetime.now(timezone.utc)

        # Если дата истощения прошла (наступил новый день по UTC), ключ снова валиден
        if now_utc.date() > exhausted_time.date():
            logger.info(f"Key at index {index} is valid again (new UTC day). Removing from exhausted list.")
            del self.exhausted_keys[index]
            return True
        else:
            # Ключ все еще истощен на сегодня
            logger.debug(f"Key at index {index} is still marked as exhausted for today ({exhausted_time.date()}).")
            return False

    def _get_next_available_index(self) -> Optional[int]:
        """Находит индекс следующего доступного (не истощенного) ключа."""
        if not self.keys:
            return None

        start_index = self.current_key_index
        for i in range(len(self.keys)):
            check_index = (start_index + i) % len(self.keys)
            if self._is_key_valid(check_index):
                logger.debug(f"Found available key at index {check_index}.")
                return check_index
        # Если прошли по кругу и не нашли валидный ключ
        logger.warning("All API keys are currently marked as exhausted.")
        return None

    def get_client(self) -> Optional[build]:
        """Возвращает YouTube API клиент с использованием доступного ключа."""
        if not self.keys:
             logger.error("Cannot get client: No API keys configured.")
             return None

        available_index = self._get_next_available_index()

        if available_index is None:
            logger.error("Cannot get client: All API keys are exhausted.")
            return None # Все ключи истощены

        self.current_key_index = available_index # Обновляем текущий индекс
        api_key = self.keys[self.current_key_index]
        self._last_used_index = self.current_key_index # Запоминаем, какой ключ выдали

        try:
            # Используем developerKey для аутентификации
            client = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, developerKey=api_key, cache_discovery=False)
            logger.info(f"Providing YouTube client using API key at index {self.current_key_index}")
            return client
        except Exception as e:
            logger.exception(f"Failed to build YouTube client with key at index {self.current_key_index}")
            # Возможно, стоит пометить ключ как "плохой" не только из-за квоты? Пока нет.
            return None # Не удалось создать клиент

    def mark_last_used_key_exhausted(self):
        """Помечает последний использованный ключ как истощенный на сегодня."""
        if self._last_used_index is not None and self._last_used_index < len(self.keys):
            now_utc = datetime.now(timezone.utc)
            self.exhausted_keys[self._last_used_index] = now_utc
            logger.warning(f"Marked API key at index {self._last_used_index} as exhausted for today ({now_utc.date()}).")
            # Сбрасываем, чтобы не пометить его снова случайно
            self._last_used_index = None
            # Сразу пытаемся переключиться на следующий
            next_available = self._get_next_available_index()
            if next_available is not None:
                 self.current_key_index = next_available
            else:
                 logger.error("Could not switch key: All keys seem exhausted after marking one.")
        else:
             logger.error("Could not mark key as exhausted: No key was recently used or index invalid.")

# Создаем единственный экземпляр менеджера, который будет использоваться во всем приложении
# Это делает его синглтоном в рамках одного процесса FastAPI
api_key_manager = ApiKeyManager()