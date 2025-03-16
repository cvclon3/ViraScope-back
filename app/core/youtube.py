from googleapiclient.discovery import build
from app.core.config import settings
from google.oauth2.credentials import Credentials  # Добавляем импорт
from google.auth.transport.requests import Request
from typing import Optional, Dict
from datetime import datetime
from datetime import timedelta
import os
import re


YOUTUBE_API_SERVICE_NAME = 'youtube'
YOUTUBE_API_VERSION = 'v3'
YOUTUBE_ANALYTICS_API_SERVICE_NAME = "youtubeAnalytics"
YOUTUBE_ANALYTICS_API_VERSION = "v2"


def get_youtube_client():
    """Создает и возвращает клиентский объект YouTube API."""
    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, developerKey=settings.youtube_api_key)


def get_analytics_client():
    """
    Создает клиент YouTube Analytics API с использованием OAuth 2.0.

    Требует наличия файла с токеном (token.json) или client_secrets.json.
    """
    credentials = None
    # Проверяем наличие файла token.json с сохраненными учетными данными
    if os.path.exists('token.json'):
        credentials = Credentials.from_authorized_user_file('token.json')
    # Если учетных данных нет или они недействительны, пытаемся обновить или получить новые
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            # Здесь предполагается, что у вас есть файл client_secrets.json,
            # полученный из Google Cloud Console.  См. документацию YouTube Analytics API.
            # Этот код предназначен для примера и может потребовать адаптации.
            # Важно: НЕ храните client_secrets.json в репозитории!
            if os.path.exists('client_secrets.json'):
               from google_auth_oauthlib.flow import InstalledAppFlow #нужно установить google-auth-oauthlib
               flow = InstalledAppFlow.from_client_secrets_file(
                   'client_secrets.json',
                   ['https://www.googleapis.com/auth/yt-analytics.readonly'] # Нужный scope
               )
               credentials = flow.run_local_server(port=settings.flow_port) # Запускаем локальный сервер для OAuth
            else:
                raise FileNotFoundError("Не найден файл client_secrets.json.  Следуйте инструкциям по настройке OAuth 2.0 для YouTube Analytics API.")
        # Сохраняем учетные данные для последующего использования
        with open('token.json', 'w') as token:
            token.write(credentials.to_json())

    return build(YOUTUBE_ANALYTICS_API_SERVICE_NAME, YOUTUBE_ANALYTICS_API_VERSION, credentials=credentials)


async def get_recent_views(video_id: str, days: int = 7) -> Optional[int]:
    """
    Получает количество просмотров видео за последние `days` дней с помощью YouTube Analytics API.

    Args:
        video_id: ID видео.
        days: Количество дней, за которые нужно получить просмотры.

    Returns:
        Количество просмотров или None, если произошла ошибка.
    """
    try:
        analytics = get_analytics_client()  # Получаем клиент Analytics API

        # Вычисляем даты начала и конца периода
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Форматируем даты в нужный формат (YYYY-MM-DD)
        start_date_str = start_date.strftime('%Y-%m-%d')
        end_date_str = end_date.strftime('%Y-%m-%d')

        # Выполняем запрос к YouTube Analytics API
        response = analytics.reports().query(
            ids=f'channel==MINE',  # Используем 'channel==MINE' для запроса по своему каналу
            startDate=start_date_str,
            endDate=end_date_str,
            metrics='views',
            filters=f'video=={video_id}',
            dimensions='video' # Важно, чтобы получать данные по конкретному видео
        ).execute()

        # Извлекаем количество просмотров (если они есть)
        if 'rows' in response and response['rows']:
             # Обычно, если данные есть, то будет только одна строка
             return response['rows'][0][0] #первая колонка - это views
        else:
            return 0 # Нет данных за этот период

    except Exception as e:
        print(f"Error in get_recent_views for video ID {video_id}: {e}")
        return None


def get_total_videos_on_channel(channel_id: str) -> Optional[int]:
    """
    Получает общее количество видео на канале.
    """
    try:
        youtube = get_youtube_client()
        channel_response = youtube.channels().list(
            part="statistics",
            id=channel_id
        ).execute()

        if not channel_response["items"]:
            return None
        channel_stats = channel_response["items"][0]["statistics"]

        return int(channel_stats['videoCount']) if 'videoCount' in channel_stats else 0

    except Exception as e:
      print(f"Error in get_total_videos_on_channel for {channel_id=}: {e}")
      return None


#Добавляем метод get_channel_info
async def get_channel_info(channel_id: str) -> Optional[Dict]:
    """
    Получает информацию о канале по его ID.

    Args:
        channel_id: ID канала.

    Returns:
        Словарь с информацией о канале или None, если произошла ошибка.
    """
    try:
        youtube = get_youtube_client()
        channel_response = youtube.channels().list(
            part="snippet,statistics",  # Запрашиваем snippet и statistics
            id=channel_id
        ).execute()

        if not channel_response["items"]:
            return None  # Канал не найден

        channel_data = channel_response["items"][0]
        snippet = channel_data["snippet"]
        statistics = channel_data["statistics"]

        channel_info = {
            'channel_title': snippet['title'],
            'channel_thumbnail': snippet['thumbnails']['high']['url'],  # Можно выбрать другое разрешение
            'channel_subscribers': int(statistics['subscriberCount']) if 'subscriberCount' in statistics else 0,
            'channel_url': f'https://www.youtube.com/channel/{channel_id}',
        }
        return channel_info

    except Exception as e:
        print(f"Error in get_channel_info for channel ID {channel_id}: {e}")
        return None


async def get_channel_views(channel_id: str) -> Optional[int]:
    """
    Получает суммарное количество просмотров на канале.
    """
    try:
        youtube = get_youtube_client()
        channel_response = youtube.channels().list(
            part="statistics",
            id=channel_id
        ).execute()

        if not channel_response["items"]:
            return None
        channel_stats = channel_response["items"][0]["statistics"]
        return int(channel_stats['viewCount']) if 'viewCount' in channel_stats else 0 #Всего просмотров

    except Exception as e:
        print(f"Error in get_channel_views for channel ID {channel_id}: {e}")
        return None


def parse_duration(duration_str: str) -> int:
    """
    Преобразует строку длительности видео в формате ISO 8601 в секунды.
    Взято из https://stackoverflow.com/questions/39825838/how-to-parse-youtube-video-duration-from-youtube-data-api-response
    """
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str)

    if not match:
        return 0

    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0

    return hours * 3600 + minutes * 60 + seconds