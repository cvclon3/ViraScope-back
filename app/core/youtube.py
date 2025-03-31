# app/core/youtube.py

# УДАЛЯЕМ get_youtube_client() и get_analytics_client()
# from googleapiclient.discovery import build
# from google.oauth2.credentials import Credentials
# from google.auth.transport.requests import Request
# import os

from app.core.config import settings
from typing import Optional, Dict
from datetime import datetime, timedelta, UTC, timezone # Добавляем timezone и UTC
import re

# Оставляем константы и вспомогательные функции
YOUTUBE_API_SERVICE_NAME = 'youtube'
YOUTUBE_API_VERSION = 'v3'
# YOUTUBE_ANALYTICS_API_SERVICE_NAME = "youtubeAnalytics" # Пока не используем
# YOUTUBE_ANALYTICS_API_VERSION = "v2"

# --- Функции get_recent_views, get_total_videos_on_channel, get_channel_info, get_channel_views ---
# --- должны теперь принимать объект 'youtube' (клиент API) как аргумент ---

async def get_recent_views(youtube, video_id: str, days: int = 7) -> Optional[int]:
    """
    Получает количество просмотров видео за последние `days` дней с помощью YouTube Analytics API.
    ПРИМЕЧАНИЕ: Эта функция требует прав yt-analytics.readonly и будет работать ТОЛЬКО для канала,
    к которому пользователь дал доступ (обычно его собственный).
    Требует другого клиента API (Analytics). Пока не используется активно.
    """
    # try:
    #     # Клиент Analytics нужно создавать отдельно с нужными credentials
    #     # analytics = build(YOUTUBE_ANALYTICS_API_SERVICE_NAME, YOUTUBE_ANALYTICS_API_VERSION, credentials=youtube.credentials)
    #     # ... остальная логика ...
    #     return 0 # Заглушка
    # except Exception as e:
    #     print(f"Error in get_recent_views for video ID {video_id}: {e}")
    #     return None
    print(f"WARNING: get_recent_views is currently disabled/requires Analytics API setup.")
    return 0 # Возвращаем 0 или None, т.к. функционал пока не активен с user credentials

def get_total_videos_on_channel(youtube, channel_id: str) -> Optional[int]:
    """
    Получает общее количество видео на канале.
    Принимает аутентифицированный клиент 'youtube'.
    """
    try:
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
      # Проверим на ошибки авторизации
      if 'HttpError 403' in str(e) and 'quotaExceeded' in str(e):
           print("Quota exceeded for the user.")
           # Можно выбросить специфическое исключение или вернуть код ошибки
      elif 'HttpError 401' in str(e) or 'HttpError 403' in str(e):
           print("Authorization error getting total videos.")
      # traceback.print_exc() # Раскомментировать для детальной отладки
      return None

async def get_channel_info(youtube, channel_id: str) -> Optional[Dict]:
    """
    Получает информацию о канале по его ID.
    Принимает аутентифицированный клиент 'youtube'.
    """
    try:
        channel_response = youtube.channels().list(
            part="snippet,statistics",
            id=channel_id
        ).execute()

        if not channel_response["items"]:
            return None

        channel_data = channel_response["items"][0]
        snippet = channel_data["snippet"]
        statistics = channel_data["statistics"]

        channel_info = {
            'channel_title': snippet['title'],
            'channel_thumbnail': snippet['thumbnails']['high']['url'],
            'channel_subscribers': int(statistics['subscriberCount']) if 'subscriberCount' in statistics else 0,
            'channel_url': f'https://www.youtube.com/channel/{channel_id}',
            # Добавим недостающие поля, если они нужны дальше
            'viewCount': int(statistics['viewCount']) if 'viewCount' in statistics else 0,
            'videoCount': int(statistics['videoCount']) if 'videoCount' in statistics else 0,
        }
        return channel_info

    except Exception as e:
        print(f"Error in get_channel_info for channel ID {channel_id}: {e}")
        # Проверим на ошибки авторизации
        if 'HttpError 403' in str(e) and 'quotaExceeded' in str(e):
             print("Quota exceeded for the user.")
        elif 'HttpError 401' in str(e) or 'HttpError 403' in str(e):
             print("Authorization error getting channel info.")
        # traceback.print_exc()
        return None


async def get_channel_views(youtube, channel_id: str) -> Optional[int]:
    """
    Получает суммарное количество просмотров на канале.
    Принимает аутентифицированный клиент 'youtube'.
    """
    try:
        # Можно использовать get_channel_info, чтобы не делать лишний запрос
        info = await get_channel_info(youtube, channel_id)
        return info.get('viewCount') if info else None
        # Или сделать отдельный запрос, если нужна только эта метрика
        # channel_response = youtube.channels().list(
        #     part="statistics",
        #     id=channel_id
        # ).execute()
        # if not channel_response["items"]:
        #     return None
        # channel_stats = channel_response["items"][0]["statistics"]
        # return int(channel_stats['viewCount']) if 'viewCount' in channel_stats else 0

    except Exception as e:
        print(f"Error in get_channel_views for channel ID {channel_id}: {e}")
        # Проверим на ошибки авторизации
        if 'HttpError 403' in str(e) and 'quotaExceeded' in str(e):
             print("Quota exceeded for the user.")
        elif 'HttpError 401' in str(e) or 'HttpError 403' in str(e):
             print("Authorization error getting channel views.")
        # traceback.print_exc()
        return None


def parse_duration(duration_str: str) -> int:
    """Преобразует строку длительности видео в формате ISO 8601 в секунды."""
    if not duration_str: # Добавим проверку на None или пустую строку
        return 0
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str)

    if not match:
        return 0

    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0

    return hours * 3600 + minutes * 60 + seconds


def get_rfc3339_date(period):
    # Убедимся, что now использует timezone.utc
    now = datetime.now(timezone.utc)

    if period == 'all_time':
        start_date = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elif period == 'last_week':
        # Идем к началу текущего дня и отнимаем 7 дней
        start_date = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7))
    elif period == 'last_month':
        # Отнимаем 30 дней (приблизительно)
        start_date = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30))
    elif period == 'last_3_month':
         start_date = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=90))
    elif period == 'last_6_month':
         start_date = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=180))
    elif period == 'last_year':
         start_date = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=365))
    else:
        # По умолчанию - all_time или ошибка
        # raise ValueError(f"Неверный период: {period}")
        start_date = datetime(1970, 1, 1, tzinfo=timezone.utc)

    # Форматируем дату в формате RFC 3339
    return start_date.strftime("%Y-%m-%dT%H:%M:%SZ")