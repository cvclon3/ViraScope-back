# app/api/search.py
import logging # Добавляем logging
from fastapi import APIRouter, Query, HTTPException, Response, status, Depends
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError # Импортируем HttpError

# --- ЗАВИСИМОСТЬ ДЛЯ ПРОВЕРКИ АУТЕНТИФИКАЦИИ ПОЛЬЗОВАТЕЛЯ ---
from app.api.auth import get_current_user
from app.models.user import User # Импортируем User для type hint

# --- ИМПОРТИРУЕМ МЕНЕДЖЕР КЛЮЧЕЙ ---
from app.core.youtube_client_manager import api_key_manager

# --- ИМПОРТЫ ЯДРА YOUTUBE И МОДЕЛЕЙ ---
from app.core.youtube import parse_duration, get_rfc3339_date, get_channel_info
# Убираем get_total_videos_on_channel т.к. оно получается через get_channel_info
from app.models.search_models import Item, SearchResponse
from urllib.parse import quote
import json
import uuid
import aiofiles
from pathlib import Path

# --- Настройка логгера ---
logger = logging.getLogger(__name__) # Используем стандартный логгер FastAPI/Uvicorn

router = APIRouter()

# --- Вспомогательные функции (без изменений) ---
def sort_json_by_key_values(json_objects, key_values, key):
    # Создаем словарь для приоритетов
    priority = {value: idx for idx, value in enumerate(key_values)}
    # Сортируем объекты по приоритету
    sorted_objects = sorted(json_objects, key=lambda x: priority.get(x[key], len(key_values)))
    return sorted_objects

def is_shorts(video_r):
    # Проверяем наличие #Shorts в названии или описании или длительность <= 3 мин
    title = video_r["snippet"].get("title", "").lower()
    description = video_r["snippet"].get("description", "").lower()
    duration = parse_duration(video_r.get('contentDetails', {}).get('duration'))
    return "#shorts" in title or "#shorts" in description or duration <= 3 * 60

def is_shorts_v(video_r):
    # Проверяем наличие #Shorts в названии или описании или длительность <= 60 сек
    title = video_r["snippet"].get("title", "").lower()
    description = video_r["snippet"].get("description", "").lower()
    duration = parse_duration(video_r.get('contentDetails', {}).get('duration'))
    return "#shorts" in title or "#shorts" in description or duration <= 60

async def save_json_to_file(data):
    json_data = json.dumps(data, indent=4)
    unique_id = str(uuid.uuid4())
    data_dir = Path("data") # Папка data для отладочных ответов API
    data_dir.mkdir(exist_ok=True)
    file_name = data_dir / f"response_{unique_id}.json"
    async with aiofiles.open(file_name, mode='w') as json_file:
        await json_file.write(json_data)

def find_object_with_next(data, key, value):
    return next((obj for obj in data if obj.get(key) == value), None)


# --- Функция для сборки объекта Item ---
async def build_search_item_obj(youtube: build, search_r, video_r, channel_id, item_type='video'):
    """
    Строит объект Item из данных поиска, видео и канала.
    Обрабатывает возможные HttpError при запросе информации о канале.
    """
    try:
        # Получаем информацию о канале с помощью функции ядра
        # get_channel_info должна пробрасывать HttpError
        channel_info = await get_channel_info(youtube, channel_id)
        if not channel_info:
            # Ошибка уже залогирована в get_channel_info
            return None # Пропускаем видео, если не можем получить инфо о канале

        # Безопасное извлечение данных видео
        stats = video_r.get('statistics', {})
        snippet = video_r.get('snippet', {})
        content_details = video_r.get('contentDetails', {})

        likes = int(stats['likeCount']) if 'likeCount' in stats else 0
        likes_hidden = 'likeCount' not in stats

        comments = int(stats['commentCount']) if 'commentCount' in stats else 0
        comments_hidden = 'commentCount' not in stats

        # Используем данные из channel_info
        channel_views = channel_info.get('viewCount', 0)
        channel_video_count = channel_info.get('videoCount', 0) # Уже int

        avg_views_per_video = float(channel_views) / float(channel_video_count) if channel_video_count > 0 else 0
        video_views = float(stats.get('viewCount', 0))

        # Fallback, если среднее 0, а просмотры есть
        if avg_views_per_video <= 0 and video_views > 0:
             avg_views_per_video = video_views

        combined_metric = video_views / avg_views_per_video if avg_views_per_video > 0 else None

        if item_type == 'video':
            video_url = f'https://www.youtube.com/watch?v={video_r["id"]}'
        elif item_type == 'shorts':
            video_url = f'https://www.youtube.com/shorts/{video_r["id"]}'
        else: # По умолчанию видео
            video_url = f'https://www.youtube.com/watch?v={video_r["id"]}'

        search_item = Item.model_validate({
            'video_id': video_r['id'],
            'title': snippet.get('title', 'No Title'),
            'thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url'),
            'published_at': snippet.get('publishedAt'), # Pydantic сам распарсит
            'views': int(stats.get('viewCount', 0)),
            'channel_title': channel_info['channel_title'],
            'channel_url': channel_info['channel_url'],
            'channel_subscribers': channel_info['channel_subscribers'],
            'video_count': channel_info['videoCount'],
            'likes': likes,
            'likes_hidden': likes_hidden,
            'comments': comments,
            'comments_hidden': comments_hidden,
            'combined_metric': combined_metric,
            'duration': parse_duration(content_details.get('duration')),
            'video_url': video_url,
            'channel_thumbnail': channel_info.get('channel_thumbnail'), # Может отсутствовать у старых каналов
        })

        return search_item.model_dump() # Возвращаем как dict для SearchResponse

    except HttpError as e:
         # Перехватываем ошибку API здесь тоже, если она проскочила из get_channel_info
         logger.error(f"HttpError in build_search_item_obj (channel_id: {channel_id}, video_id: {video_r.get('id', 'N/A')}): {e.status_code} - {e.reason}")
         # Пробрасываем ошибку выше, чтобы сработала логика ротации ключей
         raise e
    except KeyError as e:
        logger.error(f"KeyError building item for video ID {video_r.get('id', 'N/A')}: Missing key {e}")
        return None # Не критичная ошибка для всего запроса
    except Exception as e:
        logger.exception(f"Unexpected error building item for video ID {video_r.get('id', 'N/A')}: {e}")
        return None # Не критичная ошибка


# --- Функция для получения пачки видео (используется внутри эндпоинтов) ---
async def get_videos(youtube: build, encoded_query, max_results, date_published, videos_result, page_token=None):
    """
    Получает одну страницу результатов поиска и детали видео.
    Пробрасывает HttpError при ошибках API.
    """
    try:
        logger.info(f"API Call: youtube.search().list (query='{encoded_query}', date={date_published}, page_token={page_token is not None})")
        search_response_dict = youtube.search().list(
            q=encoded_query,
            part='snippet',
            type='video',
            pageToken=page_token,
            publishedAfter=date_published if date_published else None,
            maxResults=50, # Запрашиваем больше для фильтрации
        ).execute()
    except HttpError as e:
        logger.error(f"HttpError during youtube.search().list: {e.status_code} - {e.reason}")
        raise e # Пробрасываем
    except Exception as e:
        logger.exception(f"Unexpected error during youtube.search().list: {e}")
        # Неожиданные ошибки превращаем в 500
        raise HTTPException(status_code=500, detail=f"YouTube API search unexpected error: {e}")

    total_results = search_response_dict.get('pageInfo', {}).get('totalResults', 0)
    next_page_token_from_api = search_response_dict.get('nextPageToken')
    search_items = search_response_dict.get('items', [])

    logger.debug(f"Search results: {len(search_items)} items found. Total approx: {total_results}. Next page: {'Yes' if next_page_token_from_api else 'No'}")

    if not search_items:
        return videos_result, None, total_results # Нет результатов на этой странице

    video_ids = [item["id"]["videoId"] for item in search_items if item.get("id", {}).get("videoId")]
    if not video_ids:
        logger.warning("No valid video IDs found in search results page.")
        return videos_result, next_page_token_from_api, total_results

    logger.info(f"API Call: youtube.videos().list for {len(video_ids)} IDs")
    try:
        video_response = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=','.join(video_ids),
            maxResults=len(video_ids) # Запрашиваем детали для всех найденных ID
        ).execute()
        video_items = video_response.get('items', [])
    except HttpError as e:
        logger.error(f"HttpError during youtube.videos().list: {e.status_code} - {e.reason}")
        raise e # Пробрасываем
    except Exception as e:
        logger.exception(f"Unexpected error during youtube.videos().list: {e}")
        raise HTTPException(status_code=500, detail=f"YouTube API videos.list unexpected error: {e}")

    logger.debug(f"Received details for {len(video_items)} videos.")
    video_details_map = {v['id']: v for v in video_items}

    processed_count = 0
    for search_item in search_items:
        video_id = search_item.get("id", {}).get("videoId")
        channel_id = search_item.get("snippet", {}).get("channelId")
        video_detail = video_details_map.get(video_id)

        if not video_id or not channel_id or not video_detail:
            logger.debug(f"Skipping item due to missing data: video_id={video_id}, channel_id={channel_id}, has_details={bool(video_detail)}")
            continue

        # Фильтруем shorts для эндпоинта /videos
        if is_shorts_v(video_detail):
            logger.debug(f"Skipping video {video_id} in /videos search as it's identified as shorts.")
            continue

        try:
            # Передаем youtube клиент дальше
            built_item = await build_search_item_obj(
                youtube, # Текущий клиент
                search_item,
                video_detail,
                channel_id,
                item_type='video' # Указываем тип
            )
        except HttpError as e:
             # Ошибка из build_search_item_obj (вероятно, при get_channel_info)
             logger.error(f"HttpError propagated from build_search_item_obj for video {video_id}: {e.status_code}")
             raise e # Пробрасываем для ротации ключей

        if built_item:
            videos_result.append(built_item)
            processed_count += 1
            if len(videos_result) >= max_results:
                 logger.debug(f"Reached max_results ({max_results}) within get_videos. Stopping processing for this page.")
                 break # Прерываем обработку этой страницы

    logger.info(f"Processed {processed_count} valid videos from this page. Total results in list: {len(videos_result)}")
    return videos_result, next_page_token_from_api, total_results


# --- Эндпоинт поиска Видео ---
@router.get("/videos", response_model=SearchResponse)
async def search_videos(
    query: str = Query(..., description="Поисковый запрос (название видео)"),
    max_results: int = Query(50, description="Количество видео в ответе", ge=1, le=50),
    date_published_filter: str = Query('all_time', alias="date_published", description="Дата публикации (all_time, last_week, last_month, last_3_month, last_6_month, last_year)"),
    # --- Зависимость для проверки аутентификации пользователя ---
    current_user: User = Depends(get_current_user) # Проверяем, что пользователь залогинен
    # Убрали зависимость response: Response
):
    """
    Эндпоинт для поиска видео с фильтрацией. Требует аутентификации пользователя.
    Использует пул API-ключей приложения для запросов к YouTube с ротацией при ошибках квоты.
    """
    logger.info(f"User '{current_user.email}' initiated video search: query='{query}', max_results={max_results}, date='{date_published_filter}'")
    if date_published_filter not in ('all_time', 'last_week', 'last_month', 'last_3_month', 'last_6_month', 'last_year'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid value for date_published')

    encoded_query = quote(query, safe="")
    rfc3339_date = get_rfc3339_date(date_published_filter) if date_published_filter != 'all_time' else None
    videos_result = []
    next_page_token = None
    total_results_estimate = 0
    # Ограничение на количество страниц API
    max_pages_to_fetch = 1 # Пример: не более 5 страниц API по 50 результатов
    attempts = 0
    # Количество попыток = количество ключей + 1 (на случай если последний ключ тоже fail)
    max_attempts = (len(api_key_manager.keys) if api_key_manager.keys else 0) + 1

    while attempts < max_attempts:
        youtube = api_key_manager.get_client()
        if youtube is None:
            # Это произойдет, если все ключи помечены как истощенные ИЛИ ключей нет вообще
            logger.error("Failed to get YouTube client: All API keys might be exhausted or none configured.")
            # Если попытки еще не исчерпаны, а клиента нет - значит ключей нет в конфиге
            if attempts == 0 and not api_key_manager.keys:
                 raise HTTPException(status_code=500, detail="YouTube API keys are not configured on the server.")
            # Иначе, все ключи попробовали и они истощены
            raise HTTPException(status_code=503, detail="Service temporarily unavailable due to API quota limits. Please try again later.")

        current_key_index = api_key_manager._last_used_index # Запоминаем индекс для логов

        try:
            logger.info(f"Attempt {attempts + 1}/{max_attempts} using API key index {current_key_index}")
            # Сбрасываем результаты и токен ПЕРЕД началом пагинации для этой попытки
            # (если это не первая попытка)
            attempt_videos_result = []
            next_page_token = None # Начинаем пагинацию заново для этого ключа

            # Пагинация по страницам YouTube API для текущего ключа
            for page_num in range(max_pages_to_fetch):
                logger.debug(f"Fetching page {page_num + 1} (attempt {attempts + 1})")

                # Вызываем get_videos с текущим клиентом
                # Передаем attempt_videos_result для накопления результатов этой попытки
                page_videos, page_next_token, total_count = await get_videos(
                    youtube=youtube,
                    encoded_query=encoded_query,
                    max_results=max_results, # Передаем конечное желаемое число
                    date_published=rfc3339_date,
                    videos_result=list(attempt_videos_result), # Копия для изоляции
                    page_token=next_page_token
                )

                attempt_videos_result = page_videos # Обновляем результаты текущей попытки
                next_page_token = page_next_token # Токен для следующей страницы ЭТОЙ ПОПЫТКИ
                if total_count > 0:
                     total_results_estimate = total_count

                logger.debug(f"Page {page_num + 1} (attempt {attempts + 1}) completed. Results: {len(attempt_videos_result)}. Next page: {'Yes' if next_page_token else 'No'}")

                # Проверяем, достигли ли нужного количества или закончились страницы API
                if len(attempt_videos_result) >= max_results or not next_page_token:
                    logger.info(f"Stopping pagination for attempt {attempts + 1}: reached max results ({len(attempt_videos_result)}) or no more pages.")
                    break # Выходим из цикла пагинации (for page_num...)
            # --- КОНЕЦ ЦИКЛА ПАГИНАЦИИ (for page_num...) ---

            # Если мы дошли сюда без исключений HttpError, значит ключ сработал успешно для этой попытки
            logger.info(f"Successfully completed API calls with key index {current_key_index} (attempt {attempts + 1}).")
            videos_result = attempt_videos_result # Сохраняем результаты успешной попытки
            break # Выходим из цикла попыток (while attempts...)

        except HttpError as e:
            # Обрабатываем ошибку API
            logger.warning(f"HttpError occurred with key index {current_key_index} (attempt {attempts + 1}): {e.status_code} - {e.reason}")
            is_quota_error = False
            if e.status_code == 403:
                try:
                    error_content = e.content.decode('utf-8')
                    logger.debug(f"Error 403 content: {error_content}")
                    error_details = json.loads(error_content)
                    if 'error' in error_details and 'errors' in error_details['error']:
                        for error in error_details['error']['errors']:
                            if error.get('reason') == 'quotaExceeded':
                                is_quota_error = True
                                break
                except Exception as parse_err:
                    logger.warning(f"Could not parse error details for 403 error: {parse_err}")

            if is_quota_error:
                logger.warning(f"Quota exceeded for API key at index {current_key_index}.")
                api_key_manager.mark_last_used_key_exhausted()
                attempts += 1
                # Сбрасываем токен перед следующей попыткой (хотя он и так должен быть None после ошибки)
                next_page_token = None
                logger.info(f"Switching key. Starting attempt {attempts + 1}.")
                continue # Переходим к следующей попытке (while attempts...)
            elif e.status_code in [400, 404]: # Ошибки запроса или "не найдено"
                 logger.error(f"Client/Not Found Error (key index {current_key_index}): {e.status_code} - {e.reason}. Content: {e.content.decode('utf-8')}")
                 # Это не проблема ключа, а проблема запроса. Прерываемся.
                 raise HTTPException(status_code=e.status_code, detail=f"YouTube API request error: {e.reason}")
            elif e.status_code == 401 or e.status_code == 403: # Другая ошибка авторизации/доступа
                 logger.error(f"Authorization/Permission error (not quota) with key index {current_key_index}. Status: {e.status_code}. Response: {e.content.decode('utf-8')}")
                 # Можно пометить ключ как плохой, но пока выдаем 500
                 # api_key_manager.mark_last_used_key_exhausted() # Помечаем, чтобы не пробовать снова?
                 # attempts += 1 # Считаем попытку
                 # continue
                 raise HTTPException(status_code=500, detail=f"YouTube API authorization error with backend key. Please contact support.")
            else: # Другие ошибки HTTP (5xx от Google?)
                logger.error(f"Unhandled HttpError (key index {current_key_index}): {e.status_code} - {e.reason}. Content: {e.content.decode('utf-8')}")
                raise HTTPException(status_code=502, detail=f"YouTube API upstream error: {e.reason} (Status: {e.status_code})") # 502 Bad Gateway
        except Exception as e:
             # Ловим другие неожиданные ошибки
             logger.exception(f"Unexpected error during video search execution (attempt {attempts + 1})")
             raise HTTPException(status_code=500, detail=f"Internal server error during video search: {str(e)}")
    # --- КОНЕЦ ЦИКЛА ПОПЫТОК (while attempts...) ---

    # Сюда попадаем, если break сработал успешно ИЛИ если закончились попытки
    if attempts >= max_attempts:
         # Если все попытки исчерпаны (значит, все ключи fail или закончились)
         logger.error(f"Failed to complete search after {attempts} attempts. All API keys may be exhausted.")
         # Если мы так и не собрали результаты, возвращаем 503
         if not videos_result:
             raise HTTPException(status_code=503, detail="Service temporarily unavailable due to API quota limits. Please try again later.")
         else:
              # Если что-то собрали на последней неудачной попытке (маловероятно, но возможно)
              # или если успешная попытка была прервана из-за max_pages
              logger.warning(f"Returning potentially incomplete results ({len(videos_result)}) as all keys were tried or max pages reached.")

    # Обрезаем до нужного количества, если собрали больше
    final_results = videos_result[:max_results]
    logger.info(f"Returning {len(final_results)} video results to user {current_user.email}.")

    # Валидируем и возвращаем ответ
    return SearchResponse.model_validate({
        'item_count': len(final_results),
        'type': 'videos',
        'items': final_results,
    }).model_dump()


# --- Эндпоинт поиска Shorts (АНАЛОГИЧНАЯ СТРУКТУРА) ---
@router.get("/shorts", response_model=SearchResponse)
async def search_shorts(
    query: str = Query(..., description="Поисковый запрос (название шортсов)"),
    max_results: int = Query(50, description="Количество видео в ответе", ge=1, le=50),
    date_published_filter: str = Query('all_time', alias="date_published", description="Дата публикации (all_time, last_week, last_month, last_3_month, last_6_month, last_year)"),
    # --- Зависимость для проверки аутентификации ---
    current_user: User = Depends(get_current_user),
    # Убрали response: Response
):
    """
    Эндпоинт для поиска shorts с фильтрацией. Требует аутентификации пользователя.
    Использует пул API-ключей приложения для запросов к YouTube с ротацией.
    """
    logger.info(f"User '{current_user.email}' initiated shorts search: query='{query}', max_results={max_results}, date='{date_published_filter}'")
    if date_published_filter not in ('all_time', 'last_week', 'last_month', 'last_3_month', 'last_6_month', 'last_year'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid value for date_published')

    encoded_query = quote(query, safe="")
    rfc3339_date = get_rfc3339_date(date_published_filter) if date_published_filter != 'all_time' else None
    shorts_result = []
    next_page_token = None
    total_results_estimate = 0
    max_pages_to_fetch = 1 # Ограничение страниц
    attempts = 0
    max_attempts = (len(api_key_manager.keys) if api_key_manager.keys else 0) + 1

    while attempts < max_attempts:
        youtube = api_key_manager.get_client()
        if youtube is None:
            logger.error("Failed to get YouTube client for shorts: All API keys exhausted or none configured.")
            if attempts == 0 and not api_key_manager.keys:
                 raise HTTPException(status_code=500, detail="YouTube API keys are not configured on the server.")
            raise HTTPException(status_code=503, detail="Service temporarily unavailable due to API quota limits. Please try again later.")

        current_key_index = api_key_manager._last_used_index
        logger.info(f"Attempt {attempts + 1}/{max_attempts} for shorts using API key index {current_key_index}")

        try:
            attempt_shorts_result = []
            next_page_token = None # Начинаем пагинацию заново

            # Пагинация по страницам YouTube API
            for page_num in range(max_pages_to_fetch):
                logger.debug(f"Fetching shorts page {page_num + 1} (attempt {attempts + 1})")
                try:
                    # --- Запрос API поиска shorts ---
                    search_response_dict = youtube.search().list(
                        q=encoded_query,
                        part='snippet',
                        type='video',
                        videoDuration='short', # < 4 минуты (согласно API v3 docs)
                        pageToken=next_page_token,
                        publishedAfter=rfc3339_date,
                        maxResults=50, # Запрашиваем больше для фильтрации
                    ).execute()
                except HttpError as e:
                    logger.error(f"HttpError during youtube.search().list for shorts: {e.status_code} - {e.reason}")
                    raise e # Пробрасываем для обработки во внешнем try/except
                except Exception as e:
                     logger.exception(f"Unexpected error during youtube.search().list for shorts: {e}")
                     raise HTTPException(status_code=500, detail=f"YouTube API search unexpected error: {e}")

                total_results_estimate = search_response_dict.get('pageInfo', {}).get('totalResults', 0)
                page_next_token = search_response_dict.get('nextPageToken')
                search_items = search_response_dict.get('items', [])
                logger.debug(f"Search found {len(search_items)} potential shorts. Next page: {'Yes' if page_next_token else 'No'}")

                if not search_items:
                    logger.info("No more potential shorts found on this page.")
                    break # Прерываем пагинацию этой попытки

                video_ids = [item["id"]["videoId"] for item in search_items if item.get("id", {}).get("videoId")]
                if not video_ids:
                     next_page_token = page_next_token # Обновляем токен для след. итерации пагинации
                     continue # К следующей странице

                # --- Запрос деталей найденных видео ---
                try:
                    video_response = youtube.videos().list(
                        part="snippet,contentDetails,statistics",
                        id=','.join(video_ids),
                        maxResults=len(video_ids)
                    ).execute()
                    video_items = video_response.get('items', [])
                except HttpError as e:
                    logger.error(f"HttpError during youtube.videos().list for shorts details: {e.status_code} - {e.reason}")
                    raise e # Пробрасываем
                except Exception as e:
                    logger.exception(f"Unexpected error during youtube.videos().list for shorts details: {e}")
                    raise HTTPException(status_code=500, detail=f"YouTube API videos.list unexpected error: {e}")

                video_details_map = {v['id']: v for v in video_items}
                processed_count_page = 0

                # --- Обработка и фильтрация результатов ---
                for search_item in search_items:
                    video_id = search_item.get("id", {}).get("videoId")
                    channel_id = search_item.get("snippet", {}).get("channelId")
                    video_detail = video_details_map.get(video_id)

                    if not video_id or not channel_id or not video_detail: continue

                    # Используем is_shorts_v для более строгой проверки (<= 60 сек)
                    if not is_shorts_v(video_detail):
                        logger.debug(f"Skipping video {video_id} in /shorts search as it fails duration/tags check.")
                        continue

                    try:
                         built_item = await build_search_item_obj(
                            youtube, search_item, video_detail, channel_id, item_type='shorts'
                         )
                    except HttpError as e:
                         logger.error(f"HttpError from build_search_item_obj for short {video_id}: {e.status_code}")
                         raise e # Пробрасываем

                    if built_item:
                        attempt_shorts_result.append(built_item)
                        processed_count_page += 1
                        if len(attempt_shorts_result) >= max_results:
                            break # Достаточно результатов, прерываем обработку этой страницы

                logger.debug(f"Processed {processed_count_page} valid shorts from this page. Total for attempt: {len(attempt_shorts_result)}")

                # Обновляем токен для следующей страницы ЭТОЙ ПОПЫТКИ
                next_page_token = page_next_token

                # Проверяем выход из цикла обработки страницы И пагинации
                if len(attempt_shorts_result) >= max_results or not next_page_token:
                    logger.info(f"Stopping shorts pagination for attempt {attempts + 1}: reached max results ({len(attempt_shorts_result)}) or no more pages.")
                    break # Выход из цикла пагинации (for page_num...)
            # --- КОНЕЦ ЦИКЛА ПАГИНАЦИИ ---

            # Успешное завершение попытки
            logger.info(f"Successfully completed shorts API calls with key index {current_key_index} (attempt {attempts + 1}).")
            shorts_result = attempt_shorts_result # Сохраняем результат
            break # Выходим из цикла попыток (while attempts...)

        except HttpError as e:
            # Обработка ошибки HttpError (аналогично search_videos)
            logger.warning(f"HttpError occurred during shorts search with key index {current_key_index} (attempt {attempts + 1}): {e.status_code} - {e.reason}")
            is_quota_error = False
            if e.status_code == 403:
                 try:
                      error_content = e.content.decode('utf-8')
                      error_details = json.loads(error_content)
                      if 'error' in error_details and 'errors' in error_details['error']:
                           for error in error_details['error']['errors']:
                                if error.get('reason') == 'quotaExceeded':
                                     is_quota_error = True
                                     break
                 except Exception as parse_err:
                      logger.warning(f"Could not parse error details for 403 error: {parse_err}")

            if is_quota_error:
                logger.warning(f"Quota exceeded for API key at index {current_key_index} (shorts search).")
                api_key_manager.mark_last_used_key_exhausted()
                attempts += 1
                next_page_token = None # Сбрасываем пагинацию
                logger.info(f"Switching key. Starting attempt {attempts + 1}.")
                continue # К следующей попытке
            elif e.status_code in [400, 404]:
                 logger.error(f"Client/Not Found Error (key index {current_key_index}): {e.status_code} - {e.reason}. Content: {e.content.decode('utf-8')}")
                 raise HTTPException(status_code=e.status_code, detail=f"YouTube API request error: {e.reason}")
            elif e.status_code == 401 or e.status_code == 403:
                 logger.error(f"Authorization/Permission error (not quota) with key index {current_key_index}. Status: {e.status_code}. Response: {e.content.decode('utf-8')}")
                 raise HTTPException(status_code=500, detail=f"YouTube API authorization error with backend key. Please contact support.")
            else:
                logger.error(f"Unhandled HttpError (key index {current_key_index}): {e.status_code} - {e.reason}. Content: {e.content.decode('utf-8')}")
                raise HTTPException(status_code=502, detail=f"YouTube API upstream error: {e.reason} (Status: {e.status_code})")
        except Exception as e:
            logger.exception(f"Unexpected error during shorts search execution (attempt {attempts + 1})")
            raise HTTPException(status_code=500, detail=f"Internal server error during shorts search: {str(e)}")
    # --- КОНЕЦ ЦИКЛА ПОПЫТОК ---

    if attempts >= max_attempts:
         logger.error(f"Failed to complete shorts search after {attempts} attempts. All API keys may be exhausted.")
         if not shorts_result:
             raise HTTPException(status_code=503, detail="Service temporarily unavailable due to API quota limits. Please try again later.")
         else:
             logger.warning(f"Returning potentially incomplete shorts results ({len(shorts_result)}) as all keys were tried or max pages reached.")

    final_results = shorts_result[:max_results]
    logger.info(f"Returning {len(final_results)} shorts results to user {current_user.email}.")

    return SearchResponse.model_validate({
        'item_count': len(final_results),
        'type': 'shorts',
        'items': final_results,
    }).model_dump()