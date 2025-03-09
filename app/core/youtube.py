from googleapiclient.discovery import build
from app.core.config import settings  # Импортируем настройки

YOUTUBE_API_SERVICE_NAME = 'youtube'
YOUTUBE_API_VERSION = 'v3'

def get_youtube_client():
    """Создает и возвращает клиентский объект YouTube API."""
    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, developerKey=settings.youtube_api_key)