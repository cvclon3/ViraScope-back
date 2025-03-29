# app/api/videos.py

from fastapi import APIRouter, Query, HTTPException, Depends # Добавляем Depends
from typing import List, Optional, Dict
# Убираем импорт get_youtube_client и функций ядра из app.core.youtube,
# так как клиент получаем через зависимость, а функции вызываем напрямую
# from app.core.youtube import (get_youtube_client, get_recent_views,
#                               get_total_videos_on_channel, get_channel_views, parse_duration)
from app.core.youtube import parse_duration # Оставляем только parse_duration, если он еще нужен здесь
# Импортируем новую зависимость и тип клиента
from googleapiclient.discovery import build
from app.api.auth import get_user_youtube_client_via_cookie
# Импортируем функции ядра для вызова с клиентом
from app.core.youtube import get_channel_info as core_get_channel_info
from app.core.youtube import get_total_videos_on_channel as core_get_total_videos
from app.core.youtube import get_channel_views as core_get_channel_views

from app.models.video import Video # Используем вашу модель Video
import math
import datetime
import re
from urllib.parse import quote_plus
import traceback # Для отладки

router = APIRouter()

# --- ИЗМЕНЕНИЕ: get_video_info теперь async и принимает youtube клиент ---
async def get_video_info(youtube: build, video_id: str) -> Optional[Dict]:
    """
    Получает подробную информацию об одном видео по его ID,
    используя предоставленный аутентифицированный клиент YouTube API.
    """
    print(f"Fetching video info for ID: {video_id}")
    try:
        # --- ИЗМЕНЕНИЕ: Используем переданный youtube клиент ---
        video_response = youtube.videos().list(
            part='snippet,statistics,contentDetails',
            id=video_id
        ).execute()

        if not video_response.get('items'):
            print(f"Video not found: {video_id}")
            return None

        video_data = video_response['items'][0]
        snippet = video_data.get('snippet', {})
        statistics = video_data.get('statistics', {})
        content_details = video_data.get('contentDetails', {})

        channel_id = snippet.get('channelId')
        if not channel_id:
            print(f"Channel ID missing for video {video_id}")
            return None # Не можем продолжить без ID канала

        # --- ИЗМЕНЕНИЕ: Вызываем функции ядра с переданным youtube клиентом ---
        channel_info = await core_get_channel_info(youtube, channel_id)
        if not channel_info:
            print(f"Could not get channel info for {channel_id} (video {video_id})")
            # Можно решить, возвращать ли None или видео без данных канала
            # return None
            # Попробуем продолжить с тем, что есть
            channel_subscribers = 0
            total_videos = 0
            all_channel_views = 0
        else:
            channel_subscribers = channel_info.get('channel_subscribers', 0)
            # total_videos = channel_info.get('videoCount', 0) # get_channel_info возвращает videoCount
            total_videos = core_get_total_videos(youtube, channel_id) or 0 # Можно вызвать отдельно, если нужно точно
            all_channel_views = await core_get_channel_views(youtube, channel_id) or 0 # Получаем просмотры канала

        likes = int(statistics['likeCount']) if 'likeCount' in statistics else 0
        likes_hidden = 'likeCount' not in statistics
        views = int(statistics.get('viewCount', 0)) # Безопасное получение
        comments = int(statistics['commentCount']) if 'commentCount' in statistics else 0
        comments_hidden = 'commentCount' not in statistics
        duration_str = content_details.get('duration')
        duration = parse_duration(duration_str) if duration_str else 0

        average_channel_views_per_video = float(all_channel_views) / float(total_videos) if total_videos > 0 else None
        # Проверка деления на ноль и наличия просмотров
        combined_metric = float(views) / average_channel_views_per_video if average_channel_views_per_video and average_channel_views_per_video > 0 else None

        # Собираем объект Video (используя вашу модель)
        video_info_obj = Video.model_validate({
            'video_id': video_id,
            'title': snippet.get('title', 'No Title'),
            'thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
            'published_at': snippet.get('publishedAt'), # Валидатор модели обработает строку
            'views': views,
            'channel_title': snippet.get('channelTitle', 'Unknown Channel'),
            'channel_url': f'https://www.youtube.com/channel/{channel_id}',
            'channel_subscribers': channel_subscribers,
            'likes': likes,
            'likes_hidden': likes_hidden,
            # Добавляем недостающие поля из вашей модели Video
            'comments': comments,
            # 'comments_hidden': comments_hidden, # Если есть в модели
            'combined_metric': combined_metric,
            'duration': duration,
            'total_videos': total_videos,
            'video_url': f'https://www.youtube.com/watch?v={video_id}',
        })
        print(f"Successfully fetched info for video: {video_id}")
        return video_info_obj.model_dump() # Возвращаем как словарь

    except Exception as e:
        print(f"Error in get_video_info for video ID {video_id}: {e}")
        # Проверяем на ошибки квоты или авторизации
        if 'HttpError 403' in str(e) and 'quotaExceeded' in str(e):
             print(f"Quota exceeded for user while fetching info for video {video_id}.")
             # Можно выбросить HTTPException(status_code=429, ...) или вернуть None
        elif 'HttpError 401' in str(e) or ('HttpError 403' in str(e) and 'forbidden' in str(e).lower()):
             print(f"Authorization error fetching info for video {video_id}.")
             # Можно выбросить HTTPException(status_code=401, ...) или вернуть None
        else:
             # Логгируем неожиданную ошибку
             traceback.print_exc()
        return None # Возвращаем None при любой ошибке

# --- Функции get_channel_info, get_channel_views, parse_duration УДАЛЕНЫ ОТСЮДА ---
# --- Они теперь в app.core.youtube и вызываются как core_get_... ---

@router.get("/", response_model=List[Video])
async def get_videos_by_title(
    query: str = Query(..., description="Поисковый запрос (название видео)"),
    max_results: int = Query(10, description="Максимальное количество результатов", ge=1, le=50),
    min_combined_metric: Optional[float] = Query(None, description="Минимальное значение combined_metric"),
    max_combined_metric: Optional[float] = Query(None, description="Максимальное значение combined_metric"),
    min_views: Optional[int] = Query(None, description="Минимальное количество просмотров"),
    max_views: Optional[int] = Query(None, description="Максимальное количество просмотров"),
    min_channel_subscribers: Optional[int] = Query(None, description="Минимальное количество подписчиков канала"),
    max_channel_subscribers: Optional[int] = Query(None, description="Максимальное количество подписчиков канала"),
    min_duration: Optional[int] = Query(None, description="Минимальная длительность видео (в секундах)"),
    max_duration: Optional[int] = Query(None, description="Максимальная длительность видео (в секундах)"),
    min_comments: Optional[int] = Query(None, description="Минимальное количество комментариев"),
    max_comments: Optional[int] = Query(None, description="Максимальное количество комментариев"),
    min_total_videos: Optional[int] = Query(None, description="Минимальное количество видео на канале"),
    max_total_videos: Optional[int] = Query(None, description="Максимальное количество видео на канале"),
    published_date: Optional[str] = Query(None, description="Дата публикации (all_time, last_week, last_month, last_3_months, last_6_months, last_year)"),
    video_type: Optional[str] = Query("any", description="Тип видео (any, video, shorts)"),
    # --- ИЗМЕНЕНИЕ: Добавляем зависимость для получения клиента YouTube ---
    youtube: build = Depends(get_user_youtube_client_via_cookie)
):
    """
    Эндпоинт для поиска видео с фильтрацией. Использует аутентификацию пользователя.
    """
    print(f"Received filtered video search: query='{query}', max_results={max_results}, filters='...'")
    try:
        # --- ИЗМЕНЕНИЕ: Используем переданный youtube клиент ---
        encoded_query = quote_plus(query) # Используем quote_plus для URL encoding

        videos = []
        next_page_token = None
        retrieved_count = 0
        # Ограничения на выполнение, чтобы избежать слишком долгих запросов
        max_pages_search = 5 # Максимум страниц поиска YouTube
        max_total_fetch = 100 # Максимум видео, для которых будем запрашивать детали
        fetched_details_count = 0
        start_time = datetime.datetime.now(datetime.timezone.utc)
        max_execution_time_sec = 30

        for page_num in range(max_pages_search):
            if fetched_details_count >= max_total_fetch:
                print("Reached max total fetch limit.")
                break
            if (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds() > max_execution_time_sec:
                 print("Reached max execution time.")
                 break

            print(f"Searching page {page_num + 1} with token: {next_page_token}")
            try:
                 search_response = youtube.search().list(
                     q=encoded_query,
                     part='snippet',
                     type='video',
                     maxResults=min(50, max_total_fetch - fetched_details_count), # Запрашиваем до 50 или оставшееся до лимита
                     pageToken=next_page_token
                 ).execute()
            except Exception as e:
                 print(f"Error during youtube.search().list (page {page_num + 1}): {e}")
                 if 'quotaExceeded' in str(e):
                      raise HTTPException(status_code=429, detail="YouTube API quota exceeded for user.")
                 elif 'forbidden' in str(e).lower() or 'authorization' in str(e).lower():
                      raise HTTPException(status_code=403, detail="YouTube API authorization error. Please re-login.")
                 else:
                      traceback.print_exc()
                      # Прерываем поиск при ошибке API
                      break


            search_items = search_response.get('items', [])
            next_page_token = search_response.get('nextPageToken')
            print(f"Found {len(search_items)} items on page {page_num + 1}.")

            if not search_items:
                break # Больше нет результатов поиска

            for search_result in search_items:
                 if fetched_details_count >= max_total_fetch: break # Проверка лимита
                 if (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds() > max_execution_time_sec: break # Проверка времени

                 video_id = search_result.get('id', {}).get('videoId')
                 if not video_id:
                     continue

                 fetched_details_count += 1
                 # --- ИЗМЕНЕНИЕ: Вызываем get_video_info с youtube клиентом ---
                 video_info = await get_video_info(youtube, video_id)

                 if video_info:
                     # --- Применяем фильтры ---
                     should_add = True

                     # Фильтр по типу видео (Shorts)
                     # get_video_info уже вычисляет 'duration'
                     duration = video_info.get('duration', 0)
                     is_short = duration <= 60 # Простое определение по длительности

                     if video_type == "video" and is_short:
                         should_add = False
                     elif video_type == "shorts" and not is_short:
                         should_add = False

                     # Остальные фильтры (применяем только если should_add еще True)
                     if should_add and min_combined_metric is not None and (video_info.get('combined_metric') is None or video_info['combined_metric'] < min_combined_metric): should_add = False
                     if should_add and max_combined_metric is not None and video_info.get('combined_metric') is not None and video_info['combined_metric'] > max_combined_metric: should_add = False
                     if should_add and min_views is not None and video_info.get('views', 0) < min_views: should_add = False
                     if should_add and max_views is not None and video_info.get('views', 0) > max_views: should_add = False
                     if should_add and min_channel_subscribers is not None and video_info.get('channel_subscribers', 0) < min_channel_subscribers: should_add = False
                     if should_add and max_channel_subscribers is not None and video_info.get('channel_subscribers', 0) > max_channel_subscribers: should_add = False
                     if should_add and min_duration is not None and duration < min_duration: should_add = False
                     if should_add and max_duration is not None and duration > max_duration: should_add = False
                     if should_add and min_comments is not None and (video_info.get('comments') is None or video_info['comments'] < min_comments): should_add = False
                     if should_add and max_comments is not None and video_info.get('comments') is not None and video_info['comments'] > max_comments: should_add = False
                     if should_add and min_total_videos is not None and (video_info.get('total_videos') is None or video_info['total_videos'] < min_total_videos): should_add = False
                     if should_add and max_total_videos is not None and video_info.get('total_videos') is not None and video_info['total_videos'] > max_total_videos: should_add = False

                     # Фильтр по дате публикации
                     if should_add and published_date and published_date != "all_time":
                         published_at_dt = video_info.get('published_at')
                         if published_at_dt:
                              # published_at уже datetime объект из модели
                              now = datetime.datetime.now(datetime.timezone.utc)
                              delta_days = (now - published_at_dt).days
                              if published_date == "last_week" and delta_days > 7: should_add = False
                              elif published_date == "last_month" and delta_days > 30: should_add = False
                              elif published_date == "last_3_months" and delta_days > 90: should_add = False
                              elif published_date == "last_6_months" and delta_days > 180: should_add = False
                              elif published_date == "last_year" and delta_days > 365: should_add = False
                         else:
                              # Если нет даты публикации, не можем применить фильтр
                              should_add = False # Или пропускаем фильтр, зависит от логики

                     # Добавляем видео, если прошло все фильтры
                     if should_add:
                         videos.append(video_info) # video_info уже словарь
                         retrieved_count += 1
                         print(f"Added video {video_id} to results. Count: {retrieved_count}")

                 # Проверяем, не набрали ли уже нужное количество
                 if retrieved_count >= max_results:
                     break # Выход из цикла по search_items

            # Проверяем выход из внешнего цикла (по страницам)
            if retrieved_count >= max_results or not next_page_token:
                break

        print(f"Finished search. Total videos matching criteria: {retrieved_count}")
        # Возвращаем не более max_results
        # Модель Video ожидает объекты, поэтому валидируем перед возвратом
        return [Video.model_validate(v) for v in videos[:max_results]]

    except HTTPException as he:
         print(f"HTTP Exception in get_videos_by_title: {he.status_code} - {he.detail}")
         raise he # Пробрасываем HTTP исключения (например, 429, 403)
    except Exception as e:
        print(f"Unexpected error in get_videos_by_title endpoint: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error during video search: {e}")