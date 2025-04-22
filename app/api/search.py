# app/api/search.py
import logging
from fastapi import APIRouter, Query, HTTPException, Response, status, Depends
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import time # Для timestamp в limit-status
from datetime import datetime, timedelta, timezone # Для limit-status
from pydantic import BaseModel # Для limit-status

# --- Зависимости и Модели ---
from app.api.auth import get_current_user
from app.models.user import User
from app.core.youtube_client_manager import api_key_manager
from app.core.youtube import parse_duration, get_rfc3339_date, get_channel_info
from app.models.search_models import Item, SearchResponse
from app.core.rate_limiter import rate_limit_search # Наш rate limiter
from app.core.redis_client import get_redis_client # Для эндпоинта статуса
from app.core.config import settings # Для получения настроек лимита

# --- Вспомогательные утилиты ---
from urllib.parse import quote
import json
import uuid
import aiofiles
from pathlib import Path
import redis.asyncio as redis # Для type hint в limit-status
from typing import Optional

# --- Настройка логгера ---
logger = logging.getLogger(__name__)

router = APIRouter()

# --- Вспомогательные функции (без изменений) ---
def sort_json_by_key_values(json_objects, key_values, key):
    priority = {value: idx for idx, value in enumerate(key_values)}
    sorted_objects = sorted(json_objects, key=lambda x: priority.get(x[key], len(key_values)))
    return sorted_objects

def is_shorts(video_r):
    title = video_r["snippet"].get("title", "").lower()
    description = video_r["snippet"].get("description", "").lower()
    duration = parse_duration(video_r.get('contentDetails', {}).get('duration'))
    return "#shorts" in title or "#shorts" in description or duration <= 3 * 60

def is_shorts_v(video_r):
    title = video_r["snippet"].get("title", "").lower()
    description = video_r["snippet"].get("description", "").lower()
    duration = parse_duration(video_r.get('contentDetails', {}).get('duration'))
    return "#shorts" in title or "#shorts" in description or duration <= 60

async def save_json_to_file(data):
    # Сохранение ответа API для отладки (можно включать/выключать)
    # json_data = json.dumps(data, indent=4)
    # unique_id = str(uuid.uuid4())
    # data_dir = Path("data")
    # data_dir.mkdir(exist_ok=True)
    # file_name = data_dir / f"response_{unique_id}.json"
    # async with aiofiles.open(file_name, mode='w') as json_file:
    #     await json_file.write(json_data)
    pass # Отключено по умолчанию

def find_object_with_next(data, key, value):
    return next((obj for obj in data if obj.get(key) == value), None)


# --- Функция для сборки объекта Item ---
async def build_search_item_obj(youtube: build, search_r, video_r, channel_id, item_type='video'):
    """
    Строит объект Item из данных поиска, видео и канала.
    Обрабатывает возможные HttpError при запросе информации о канале.
    """
    try:
        channel_info = await get_channel_info(youtube, channel_id)
        if not channel_info:
            return None

        stats = video_r.get('statistics', {})
        snippet = video_r.get('snippet', {})
        content_details = video_r.get('contentDetails', {})

        likes = int(stats['likeCount']) if 'likeCount' in stats else 0
        likes_hidden = 'likeCount' not in stats

        comments = int(stats['commentCount']) if 'commentCount' in stats else 0
        comments_hidden = 'commentCount' not in stats

        channel_views = channel_info.get('viewCount', 0)
        channel_video_count = channel_info.get('videoCount', 0)

        avg_views_per_video = float(channel_views) / float(channel_video_count) if channel_video_count > 0 else 0
        video_views = float(stats.get('viewCount', 0))

        if avg_views_per_video <= 0 and video_views > 0:
             avg_views_per_video = video_views

        combined_metric = video_views / avg_views_per_video if avg_views_per_video > 0 else None

        video_url = f'https://www.youtube.com/watch?v={video_r["id"]}'
        if item_type == 'shorts':
            video_url = f'https://www.youtube.com/shorts/{video_r["id"]}'

        # Используем .get() для большей устойчивости к отсутствующим полям
        search_item = Item.model_validate({
            'video_id': video_r.get('id'),
            'title': snippet.get('title', 'No Title'),
            'thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url'),
            'published_at': snippet.get('publishedAt'),
            'views': int(stats.get('viewCount', 0)),
            'channel_title': channel_info.get('channel_title', 'Unknown Channel'),
            'channel_url': channel_info.get('channel_url', f'https://www.youtube.com/channel/{channel_id}'),
            'channel_subscribers': channel_info.get('channel_subscribers', 0),
            'video_count': channel_info.get('videoCount', 0),
            'likes': likes,
            'likes_hidden': likes_hidden,
            'comments': comments,
            'comments_hidden': comments_hidden,
            'combined_metric': combined_metric,
            'duration': parse_duration(content_details.get('duration')),
            'video_url': video_url,
            'channel_thumbnail': channel_info.get('channel_thumbnail'),
        })
        return search_item.model_dump()

    except HttpError as e:
         logger.error(f"HttpError in build_search_item_obj (channel_id: {channel_id}, video_id: {video_r.get('id', 'N/A')}): {e.status_code} - {e.reason}")
         raise e
    except KeyError as e:
        logger.error(f"KeyError building item for video ID {video_r.get('id', 'N/A')}: Missing key {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error building item for video ID {video_r.get('id', 'N/A')}: {e}")
        return None


# --- Функция для получения пачки видео ---
async def get_videos_page(youtube: build, encoded_query, max_results_target, date_published, current_results, page_token=None):
    """
    Получает одну страницу результатов поиска видео и их детали.
    Фильтрует shorts.
    Пробрасывает HttpError при ошибках API.
    Возвращает: (list_of_items_on_page, next_page_token, total_results_estimate)
    """
    try:
        logger.info(f"API Call: youtube.search().list (videos, query='{encoded_query}', page_token={page_token is not None})")
        search_response_dict = youtube.search().list(
            q=encoded_query, part='snippet', type='video',
            pageToken=page_token, publishedAfter=date_published, maxResults=50
        ).execute()
    except HttpError as e:
        logger.error(f"HttpError during youtube.search().list: {e.status_code} - {e.reason}")
        raise e
    except Exception as e:
        logger.exception(f"Unexpected error during youtube.search().list: {e}")
        raise HTTPException(status_code=500, detail=f"YouTube API search unexpected error: {e}")

    total_results = search_response_dict.get('pageInfo', {}).get('totalResults', 0)
    next_page_token_from_api = search_response_dict.get('nextPageToken')
    search_items = search_response_dict.get('items', [])
    logger.debug(f"Search page results: {len(search_items)} items found. Next page: {'Yes' if next_page_token_from_api else 'No'}")

    if not search_items:
        return [], None, total_results

    video_ids = [item["id"]["videoId"] for item in search_items if item.get("id", {}).get("videoId")]
    if not video_ids:
        return [], next_page_token_from_api, total_results

    logger.info(f"API Call: youtube.videos().list for {len(video_ids)} IDs")
    try:
        video_response = youtube.videos().list(
            part="snippet,contentDetails,statistics", id=','.join(video_ids), maxResults=len(video_ids)
        ).execute()
        video_items = video_response.get('items', [])
    except HttpError as e:
        logger.error(f"HttpError during youtube.videos().list: {e.status_code} - {e.reason}")
        raise e
    except Exception as e:
        logger.exception(f"Unexpected error during youtube.videos().list: {e}")
        raise HTTPException(status_code=500, detail=f"YouTube API videos.list unexpected error: {e}")

    video_details_map = {v['id']: v for v in video_items}
    page_results = []
    processed_count = 0

    for search_item in search_items:
        video_id = search_item.get("id", {}).get("videoId")
        channel_id = search_item.get("snippet", {}).get("channelId")
        video_detail = video_details_map.get(video_id)

        if not video_id or not channel_id or not video_detail: continue

        # Фильтруем shorts
        if is_shorts_v(video_detail):
            logger.debug(f"Skipping video {video_id} in /videos search as it's shorts.")
            continue

        try:
            built_item = await build_search_item_obj(youtube, search_item, video_detail, channel_id, 'video')
        except HttpError as e:
            logger.error(f"HttpError from build_search_item_obj for video {video_id}: {e.status_code}")
            raise e # Пробрасываем для ротации

        if built_item:
            page_results.append(built_item)
            processed_count += 1
            # Проверяем общее количество с учетом уже имеющихся результатов
            if len(current_results) + len(page_results) >= max_results_target:
                 logger.debug(f"Reached max_results_target ({max_results_target}) within get_videos_page. Stopping processing.")
                 break # Прерываем обработку этой страницы

    logger.info(f"Processed {processed_count} valid videos from this API page.")
    return page_results, next_page_token_from_api, total_results


# --- Функция для получения пачки Shorts ---
async def get_shorts_page(youtube: build, encoded_query, max_results_target, date_published, current_results, page_token=None):
    """
    Получает одну страницу результатов поиска shorts и их детали.
    Фильтрует не-shorts.
    Пробрасывает HttpError при ошибках API.
    Возвращает: (list_of_items_on_page, next_page_token, total_results_estimate)
    """
    try:
        logger.info(f"API Call: youtube.search().list (shorts, query='{encoded_query}', page_token={page_token is not None})")
        search_response_dict = youtube.search().list(
            q=encoded_query, part='snippet', type='video', videoDuration='short', # videoDuration может быть неточным
            pageToken=page_token, publishedAfter=date_published, maxResults=50
        ).execute()
    except HttpError as e:
        logger.error(f"HttpError during youtube.search().list (shorts): {e.status_code} - {e.reason}")
        raise e
    except Exception as e:
        logger.exception(f"Unexpected error during youtube.search().list (shorts): {e}")
        raise HTTPException(status_code=500, detail=f"YouTube API search unexpected error: {e}")

    total_results = search_response_dict.get('pageInfo', {}).get('totalResults', 0)
    next_page_token_from_api = search_response_dict.get('nextPageToken')
    search_items = search_response_dict.get('items', [])
    logger.debug(f"Search page results (shorts): {len(search_items)} items found. Next page: {'Yes' if next_page_token_from_api else 'No'}")

    if not search_items:
        return [], None, total_results

    video_ids = [item["id"]["videoId"] for item in search_items if item.get("id", {}).get("videoId")]
    if not video_ids:
        return [], next_page_token_from_api, total_results

    logger.info(f"API Call: youtube.videos().list for {len(video_ids)} IDs (shorts)")
    try:
        video_response = youtube.videos().list(
            part="snippet,contentDetails,statistics", id=','.join(video_ids), maxResults=len(video_ids)
        ).execute()
        video_items = video_response.get('items', [])
    except HttpError as e:
        logger.error(f"HttpError during youtube.videos().list (shorts): {e.status_code} - {e.reason}")
        raise e
    except Exception as e:
        logger.exception(f"Unexpected error during youtube.videos().list (shorts): {e}")
        raise HTTPException(status_code=500, detail=f"YouTube API videos.list unexpected error: {e}")

    video_details_map = {v['id']: v for v in video_items}
    page_results = []
    processed_count = 0

    for search_item in search_items:
        video_id = search_item.get("id", {}).get("videoId")
        channel_id = search_item.get("snippet", {}).get("channelId")
        video_detail = video_details_map.get(video_id)

        if not video_id or not channel_id or not video_detail: continue

        # Используем is_shorts_v для строгой проверки
        if not is_shorts_v(video_detail):
            logger.debug(f"Skipping video {video_id} in /shorts search as it fails duration/tags check.")
            continue

        try:
            built_item = await build_search_item_obj(youtube, search_item, video_detail, channel_id, 'shorts')
        except HttpError as e:
            logger.error(f"HttpError from build_search_item_obj for short {video_id}: {e.status_code}")
            raise e # Пробрасываем

        if built_item:
            page_results.append(built_item)
            processed_count += 1
            if len(current_results) + len(page_results) >= max_results_target:
                 logger.debug(f"Reached max_results_target ({max_results_target}) within get_shorts_page. Stopping processing.")
                 break

    logger.info(f"Processed {processed_count} valid shorts from this API page.")
    return page_results, next_page_token_from_api, total_results


# --- Эндпоинт поиска Видео ---
@router.get("/videos", response_model=SearchResponse)
async def search_videos(
    query: str = Query(..., description="Поисковый запрос (название видео)"),
    max_results: int = Query(50, description="Количество видео в ответе", ge=1, le=100), # Увеличил макс до 100
    date_published_filter: str = Query('all_time', alias="date_published", description="Дата публикации (all_time, last_week, last_month, last_3_month, last_6_month, last_year)"),
    current_user: User = Depends(get_current_user),
    _rate_limit: bool = Depends(rate_limit_search) # Применяем rate limiter
):
    """
    Поиск видео YouTube с фильтрацией. Требует аутентификации.
    Применяется ограничение частоты запросов.
    Использует пул API-ключей приложения с ротацией при ошибках квоты.
    """
    logger.info(f"User '{current_user.email}' /videos search: query='{query}', max={max_results}, date='{date_published_filter}'")
    if date_published_filter not in ('all_time', 'last_week', 'last_month', 'last_3_month', 'last_6_month', 'last_year'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid value for date_published')

    encoded_query = quote(query, safe="")
    rfc3339_date = get_rfc3339_date(date_published_filter) if date_published_filter != 'all_time' else None
    all_results = []
    next_page_token = None
    # Ограничение страниц API для одного запроса
    max_pages_to_fetch = 1 # Можно вынести в конфиг
    attempts = 0
    max_attempts = (len(api_key_manager.keys) if api_key_manager.keys else 0) + 1

    while attempts < max_attempts:
        youtube = api_key_manager.get_client()
        if youtube is None:
            logger.error(f"Failed to get YouTube client (attempt {attempts + 1}). Keys exhausted or not configured.")
            if attempts == 0 and not api_key_manager.keys:
                 raise HTTPException(status_code=500, detail="YouTube API keys are not configured.")
            raise HTTPException(status_code=503, detail="Service temporarily unavailable due to API quota limits.")

        current_key_index = api_key_manager._last_used_index
        logger.info(f"Attempt {attempts + 1}/{max_attempts} using API key index {current_key_index}")

        try:
            # Начинаем сбор результатов для ЭТОЙ попытки
            attempt_results = []
            next_page_token = None # Сбрасываем пагинацию для новой попытки

            for page_num in range(max_pages_to_fetch):
                logger.debug(f"Fetching videos page {page_num + 1} (attempt {attempts + 1})")

                page_items, page_next_token, _ = await get_videos_page(
                    youtube=youtube,
                    encoded_query=encoded_query,
                    max_results_target=max_results,
                    date_published=rfc3339_date,
                    current_results=attempt_results, # Передаем текущие результаты этой попытки
                    page_token=next_page_token
                )

                attempt_results.extend(page_items)
                next_page_token = page_next_token # Токен для следующей страницы этой попытки

                logger.debug(f"Page {page_num + 1} completed. Results this attempt: {len(attempt_results)}. Next page: {'Yes' if next_page_token else 'No'}")

                if len(attempt_results) >= max_results or not next_page_token:
                    logger.info(f"Stopping pagination for attempt {attempts + 1}: reached max results ({len(attempt_results)}) or no more pages.")
                    break # Выходим из цикла пагинации (for page_num...)
            # --- КОНЕЦ ЦИКЛА ПАГИНАЦИИ ---

            logger.info(f"Successfully completed API calls with key index {current_key_index} (attempt {attempts + 1}). Found {len(attempt_results)} items.")
            all_results = attempt_results # Сохраняем результаты успешной попытки
            break # Выходим из цикла попыток (while attempts...)

        except HttpError as e:
            logger.warning(f"HttpError with key index {current_key_index} (attempt {attempts + 1}): {e.status_code} - {e.reason}")
            is_quota_error = False
            if e.status_code == 403:
                try:
                    error_details = json.loads(e.content.decode('utf-8'))
                    is_quota_error = any(err.get('reason') == 'quotaExceeded' for err in error_details.get('error', {}).get('errors', []))
                except: pass # Ignore parsing errors

            if is_quota_error:
                logger.warning(f"Quota exceeded for API key index {current_key_index}.")
                api_key_manager.mark_last_used_key_exhausted()
                attempts += 1
                logger.info(f"Switching key. Starting attempt {attempts + 1}.")
                continue # К следующей попытке
            elif e.status_code in [400, 404]:
                 logger.error(f"Client/Not Found Error (key {current_key_index}): {e.status_code} - {e.reason}. Content: {e.content.decode('utf-8')}")
                 raise HTTPException(status_code=e.status_code, detail=f"YouTube API request error: {e.reason}")
            elif e.status_code in [401, 403]:
                 logger.error(f"Auth/Permission Error (key {current_key_index}, not quota): {e.status_code}. Content: {e.content.decode('utf-8')}")
                 raise HTTPException(status_code=500, detail="YouTube API authorization error with backend key.")
            else:
                logger.error(f"Unhandled HttpError (key {current_key_index}): {e.status_code}. Content: {e.content.decode('utf-8')}")
                raise HTTPException(status_code=502, detail=f"YouTube API upstream error: {e.reason}")
        except Exception as e:
             logger.exception(f"Unexpected error during video search (attempt {attempts + 1})")
             raise HTTPException(status_code=500, detail=f"Internal server error during video search: {str(e)}")
    # --- КОНЕЦ ЦИКЛА ПОПЫТОК ---

    if attempts >= max_attempts:
         logger.error(f"Failed to complete video search after {attempts} attempts. All keys exhausted?")
         if not all_results: # Если совсем ничего не нашли
             raise HTTPException(status_code=503, detail="Service temporarily unavailable due to API quota limits.")
         else: # Если нашли что-то, но не смогли завершить (редко)
              logger.warning(f"Returning potentially incomplete video results ({len(all_results)})")

    final_results = all_results[:max_results] # Обрезаем, если нашли больше нужного
    logger.info(f"Returning {len(final_results)} video results to user {current_user.email}.")
    return SearchResponse(item_count=len(final_results), type='videos', items=final_results)


# --- Эндпоинт поиска Shorts ---
@router.get("/shorts", response_model=SearchResponse)
async def search_shorts(
    query: str = Query(..., description="Поисковый запрос (название шортсов)"),
    max_results: int = Query(50, description="Количество видео в ответе", ge=1, le=100),
    date_published_filter: str = Query('all_time', alias="date_published", description="Дата публикации (all_time, last_week, last_month, last_3_month, last_6_month, last_year)"),
    current_user: User = Depends(get_current_user),
    _rate_limit: bool = Depends(rate_limit_search) # Применяем rate limiter
):
    """
    Поиск shorts YouTube с фильтрацией. Требует аутентификации.
    Применяется ограничение частоты запросов.
    Использует пул API-ключей приложения с ротацией.
    """
    logger.info(f"User '{current_user.email}' /shorts search: query='{query}', max={max_results}, date='{date_published_filter}'")
    if date_published_filter not in ('all_time', 'last_week', 'last_month', 'last_3_month', 'last_6_month', 'last_year'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid value for date_published')

    encoded_query = quote(query, safe="")
    rfc3339_date = get_rfc3339_date(date_published_filter) if date_published_filter != 'all_time' else None
    all_results = []
    next_page_token = None
    max_pages_to_fetch = 1
    attempts = 0
    max_attempts = (len(api_key_manager.keys) if api_key_manager.keys else 0) + 1

    while attempts < max_attempts:
        youtube = api_key_manager.get_client()
        if youtube is None:
            logger.error(f"Failed to get YouTube client for shorts (attempt {attempts + 1}).")
            if attempts == 0 and not api_key_manager.keys:
                 raise HTTPException(status_code=500, detail="YouTube API keys are not configured.")
            raise HTTPException(status_code=503, detail="Service temporarily unavailable due to API quota limits.")

        current_key_index = api_key_manager._last_used_index
        logger.info(f"Attempt {attempts + 1}/{max_attempts} for shorts using API key index {current_key_index}")

        try:
            attempt_results = []
            next_page_token = None # Сброс пагинации для новой попытки

            for page_num in range(max_pages_to_fetch):
                logger.debug(f"Fetching shorts page {page_num + 1} (attempt {attempts + 1})")

                page_items, page_next_token, _ = await get_shorts_page(
                    youtube=youtube,
                    encoded_query=encoded_query,
                    max_results_target=max_results,
                    date_published=rfc3339_date,
                    current_results=attempt_results,
                    page_token=next_page_token
                )

                attempt_results.extend(page_items)
                next_page_token = page_next_token

                logger.debug(f"Shorts page {page_num + 1} completed. Results this attempt: {len(attempt_results)}. Next page: {'Yes' if next_page_token else 'No'}")

                if len(attempt_results) >= max_results or not next_page_token:
                    logger.info(f"Stopping shorts pagination for attempt {attempts + 1}: reached max results ({len(attempt_results)}) or no more pages.")
                    break
            # --- КОНЕЦ ЦИКЛА ПАГИНАЦИИ ---

            logger.info(f"Successfully completed shorts API calls with key index {current_key_index} (attempt {attempts + 1}). Found {len(attempt_results)} items.")
            all_results = attempt_results
            break # Выходим из цикла попыток

        except HttpError as e:
            # Обработка ошибок HttpError (аналогично /videos)
            logger.warning(f"HttpError with key index {current_key_index} (shorts attempt {attempts + 1}): {e.status_code} - {e.reason}")
            is_quota_error = False
            if e.status_code == 403:
                try:
                    error_details = json.loads(e.content.decode('utf-8'))
                    is_quota_error = any(err.get('reason') == 'quotaExceeded' for err in error_details.get('error', {}).get('errors', []))
                except: pass

            if is_quota_error:
                logger.warning(f"Quota exceeded for API key index {current_key_index} (shorts).")
                api_key_manager.mark_last_used_key_exhausted()
                attempts += 1
                logger.info(f"Switching key. Starting attempt {attempts + 1}.")
                continue
            elif e.status_code in [400, 404]:
                 logger.error(f"Client/Not Found Error (key {current_key_index}, shorts): {e.status_code} - {e.reason}. Content: {e.content.decode('utf-8')}")
                 raise HTTPException(status_code=e.status_code, detail=f"YouTube API request error: {e.reason}")
            elif e.status_code in [401, 403]:
                 logger.error(f"Auth/Permission Error (key {current_key_index}, not quota, shorts): {e.status_code}. Content: {e.content.decode('utf-8')}")
                 raise HTTPException(status_code=500, detail="YouTube API authorization error with backend key.")
            else:
                logger.error(f"Unhandled HttpError (key {current_key_index}, shorts): {e.status_code}. Content: {e.content.decode('utf-8')}")
                raise HTTPException(status_code=502, detail=f"YouTube API upstream error: {e.reason}")
        except Exception as e:
             logger.exception(f"Unexpected error during shorts search (attempt {attempts + 1})")
             raise HTTPException(status_code=500, detail=f"Internal server error during shorts search: {str(e)}")
    # --- КОНЕЦ ЦИКЛА ПОПЫТОК ---

    if attempts >= max_attempts:
         logger.error(f"Failed to complete shorts search after {attempts} attempts.")
         if not all_results:
             raise HTTPException(status_code=503, detail="Service temporarily unavailable due to API quota limits.")
         else:
             logger.warning(f"Returning potentially incomplete shorts results ({len(all_results)})")

    final_results = all_results[:max_results]
    logger.info(f"Returning {len(final_results)} shorts results to user {current_user.email}.")
    return SearchResponse(item_count=len(final_results), type='shorts', items=final_results)


# --- Эндпоинт статуса лимита ---
class SearchLimitStatusResponse(BaseModel):
    limit: int
    remaining: int
    window_seconds: int
    resets_at_timestamp: Optional[int] = None # Время сброса в виде Unix timestamp (секунды UTC)
    resets_at_datetime_utc: Optional[datetime] = None # Время сброса в виде datetime UTC

@router.get("/limit-status", response_model=SearchLimitStatusResponse)
async def get_search_limit_status(
    user: User = Depends(get_current_user),
    redis_client: redis.Redis = Depends(get_redis_client) # Используем async клиент
):
    """
    Возвращает текущий статус лимита на поиск для аутентифицированного пользователя.
    """
    limit = settings.search_rate_limit_count
    window = settings.search_rate_limit_window_seconds
    # Формируем ключ аналогично rate_limiter
    key = f"rate_limit:user:{user.id}:search"
    remaining = limit # По умолчанию все попытки доступны
    resets_at_ts = None
    resets_at_dt = None

    try:
        # Pipeline для получения GET и TTL за один раунд-трип
        async with redis_client.pipeline(transaction=False) as pipe:
            pipe.get(key)
            pipe.ttl(key)
            results = await pipe.execute()

        current_count_str = results[0]
        ttl = results[1]

        current_count = 0
        if current_count_str:
             try:
                  current_count = int(current_count_str)
             except (ValueError, TypeError):
                  logger.warning(f"Invalid count value in Redis for key {key}: {current_count_str}")

        # Рассчитываем оставшиеся попытки
        # Если current_count > limit, значит лимит исчерпан, remaining = 0
        remaining = max(0, limit - current_count)

        # Рассчитываем время сброса, если ключ существует и имеет TTL
        if ttl >= 0: # ttl >= 0 означает, что ключ существует и имеет TTL
            now_ts = time.time()
            resets_at_ts = int(now_ts + ttl)
            try:
                # Используем timezone.utc для корректного datetime
                resets_at_dt = datetime.fromtimestamp(resets_at_ts, tz=timezone.utc)
            except (ValueError, OSError): # На случай очень больших TTL
                 logger.warning(f"Could not convert reset timestamp {resets_at_ts} to datetime.")
                 resets_at_dt = None # Не можем рассчитать datetime
        elif ttl == -1:
             # Ключ существует, но без TTL - это нештатная ситуация для нашей логики
             logger.warning(f"Rate limit key {key} exists but has no TTL (-1). Remaining calculated, but reset time is unknown.")
        # Если ttl == -2 (ключ не найден), то remaining = limit, reset = None (установлены по умолчанию)

        logger.debug(f"Limit status for user {user.id}: Count={current_count}, TTL={ttl}, Remaining={remaining}, ResetsAt={resets_at_dt}")

        return SearchLimitStatusResponse(
            limit=limit,
            remaining=remaining,
            window_seconds=window,
            resets_at_timestamp=resets_at_ts,
            resets_at_datetime_utc=resets_at_dt
        )

    except redis.RedisError as e:
        logger.error(f"Redis error getting rate limit status for user {user.id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not retrieve rate limit status due to cache service error."
        )
    except Exception as e:
        logger.exception(f"Unexpected error getting rate limit status for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error retrieving rate limit status."
        )