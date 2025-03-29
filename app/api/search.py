# app/api/search.py
from fastapi import APIRouter, Query, HTTPException, Response, status, Depends # Добавляем Depends
from googleapiclient.discovery import build # Для тайпхинта клиента
from app.api.auth import get_user_youtube_client_via_cookie # Наша новая зависимость
# Убираем импорт get_youtube_client из app.core.youtube
# from app.core.youtube import get_youtube_client, parse_duration, get_rfc3339_date
from app.core.youtube import parse_duration, get_rfc3339_date, get_channel_info, get_total_videos_on_channel # Импортируем нужные функции ядра
from app.models.search_models import Item, SearchResponse
from urllib.parse import quote
import json
import uuid
import aiofiles
from pathlib import Path

router = APIRouter()


def sort_json_by_key_values(json_objects, key_values, key):
    # Создаем словарь для приоритетов
    priority = {value: idx for idx, value in enumerate(key_values)}

    # Сортируем объекты по приоритету
    sorted_objects = sorted(json_objects, key=lambda x: priority.get(x[key], len(key_values)))

    return sorted_objects


def is_shorts(video_r):
    # Проверяем наличие #Shorts в названии или описании
    title = video_r["snippet"]["title"]
    description = video_r["snippet"]["description"]
    duration = parse_duration(video_r['contentDetails']['duration'])
    return "#shorts" in title.lower() or "#shorts" in description.lower() or duration <= 3*60


def is_shorts_v(video_r):
    # Проверяем наличие #Shorts в названии или описании
    title = video_r["snippet"]["title"]
    description = video_r["snippet"]["description"]
    duration = parse_duration(video_r['contentDetails']['duration'])
    return "#shorts" in title.lower() or "#shorts" in description.lower() or duration <= 60


async def save_json_to_file(data):
    # Преобразуем словарь в JSON-строку
    json_data = json.dumps(data, indent=4)

    # Генерируем уникальный идентификатор
    unique_id = str(uuid.uuid4())

    # Создаем путь к папке data
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)  # Создаем папку, если её нет

    # Создаем имя файла
    file_name = data_dir / f"response_{unique_id}.json"

    # Асинхронно записываем данные в файл
    async with aiofiles.open(file_name, mode='w') as json_file:
        await json_file.write(json_data)


def find_object_with_next(data, key, value):
    return next((obj for obj in data if obj.get(key) == value), None)


# --- Остальные функции (sort_json_by_key_values, save_json_to_file, find_object_with_next) остаются ---
# ...

# --- ИЗМЕНЕНИЕ: build_search_item_obj теперь принимает объект youtube ---
async def build_search_item_obj(youtube: build, search_r, video_r, channel_id, item_type='video'):
    try:
        # Получаем информацию о канале с помощью функции ядра
        channel_info = await get_channel_info(youtube, channel_id)
        if not channel_info:
            print(f"Could not get channel info for {channel_id}")
            return None # Пропускаем видео, если не можем получить инфо о канале

        # Данные видео уже есть в video_r
        likes = int(video_r['statistics']['likeCount']) if 'likeCount' in video_r['statistics'] else 0
        likes_hidden = 'likeCount' not in video_r['statistics']

        comments = int(video_r['statistics']['commentCount']) if 'commentCount' in video_r['statistics'] else 0
        comments_hidden = 'commentCount' not in video_r['statistics']

        # Используем данные из channel_info
        channel_views = channel_info.get('viewCount', 0)
        channel_video_count = channel_info.get('videoCount', -1)

        avg_views_per_video = float(channel_views) / float(channel_video_count) if channel_video_count > 0 else 0
        if avg_views_per_video <= 0:
            # Используем просмотры текущего видео как fallback, если среднее посчитать не удалось
             avg_views_per_video = float(video_r['statistics'].get('viewCount', 0))

        # Убедимся, что просмотры видео - это число
        video_views = float(video_r['statistics'].get('viewCount', 0))
        combined_metric = video_views / avg_views_per_video if avg_views_per_video > 0 else None


        if item_type == 'video':
            video_url = f'https://www.youtube.com/watch?v={video_r["id"]}'
        elif item_type == 'shorts':
            video_url = f'https://www.youtube.com/shorts/{video_r["id"]}'
        else:
            video_url = f'https://www.youtube.com/watch?v={video_r["id"]}'

        search_item = Item.model_validate({
            'video_id': video_r['id'],
            'title': video_r['snippet']['title'],
            'thumbnail': video_r['snippet']['thumbnails']['high']['url'],
            'published_at': video_r['snippet']['publishedAt'],
            'views': int(video_r['statistics'].get('viewCount', 0)), # Безопасное получение просмотров
            'channel_title': channel_info['channel_title'],
            'channel_url': channel_info['channel_url'],
            'channel_subscribers': channel_info['channel_subscribers'],
            'video_count': channel_info['videoCount'], # Используем videoCount из channel_info
            'likes': likes,
            'likes_hidden': likes_hidden,
            'comments': comments,
            'comments_hidden': comments_hidden,
            'combined_metric': combined_metric,
            'duration': parse_duration(video_r.get('contentDetails', {}).get('duration')), # Безопасное получение duration
            'video_url': video_url,
            'channel_thumbnail': channel_info['channel_thumbnail'],
        })

        return search_item.model_dump()

    except KeyError as e:
        print(f"KeyError in build_search_item_obj for video ID {video_r.get('id', 'N/A')}: Missing key {e}")
        # traceback.print_exc()
        return None
    except Exception as e:
        print(f"Error in build_search_item_obj for video ID {video_r.get('id', 'N/A')}: {e}")
        # traceback.print_exc()
        return None

# --- Функции is_shorts, is_shorts_v остаются ---
# ...

# --- ИЗМЕНЕНИЕ: get_videos теперь принимает объект youtube ---
async def get_videos(youtube: build, response: Response, encoded_query, max_results, date_published, videos_result, page_token=None):
    try:
        print(f"Searching YouTube with query: '{encoded_query}', date: {date_published}, page_token: {page_token}")
        search_response_dict = youtube.search().list(
            q=encoded_query,
            part='snippet',
            type='video',
            pageToken=page_token,
            publishedAfter=date_published if date_published else None, # Убедимся, что None передается, если дата не указана
            maxResults=50, # Запрашиваем 50, чтобы иметь запас для фильтрации
        ).execute()
    except Exception as e:
        print(f"Error during youtube.search().list: {e}")
        # Проверяем на ошибки квоты или авторизации
        if 'HttpError 403' in str(e) and 'quotaExceeded' in str(e):
             raise HTTPException(status_code=429, detail="YouTube API quota exceeded for user.")
        elif 'HttpError 401' in str(e) or 'HttpError 403' in str(e):
             raise HTTPException(status_code=401, detail="YouTube API authorization error. Please re-login.")
        else:
             raise HTTPException(status_code=500, detail=f"YouTube API search error: {e}")


    total_results = search_response_dict.get('pageInfo', {}).get('totalResults', 0)
    next_page_token_from_api = search_response_dict.get('nextPageToken')
    search_items = search_response_dict.get('items', [])

    print(f"Found {len(search_items)} items in search results. Total results: {total_results}. Next page token: {next_page_token_from_api}")


    if not search_items:
        # Не ошибка, просто нет результатов
        print("No search results found for this page.")
        return videos_result, None, total_results # Возвращаем None как next_page_token

    video_ids = [item["id"]["videoId"] for item in search_items if item.get("id", {}).get("videoId")]
    # channel_ids = list({item["snippet"]["channelId"] for item in search_items if item.get("snippet", {}).get("channelId")}) # Уникальные ID каналов

    if not video_ids:
        print("No valid video IDs found in search results.")
        return videos_result, next_page_token_from_api, total_results

    print(f"Fetching details for {len(video_ids)} video IDs: {video_ids}")
    try:
        video_response = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=','.join(video_ids),
        ).execute()
        video_items = video_response.get('items', [])
    except Exception as e:
        print(f"Error during youtube.videos().list: {e}")
        # Обработка ошибок API при запросе деталей видео
        # Можно пропустить эту пачку или вернуть ошибку
        return videos_result, next_page_token_from_api, total_results # Пропускаем, если не можем получить детали

    print(f"Received details for {len(video_items)} videos.")

    # Сопоставляем видео из поиска с их деталями
    video_details_map = {v['id']: v for v in video_items}

    # --- УДАЛЕНО: Запрос channel_response, т.к. get_channel_info будет вызываться в build_search_item_obj ---
    # channel_response = youtube.channels().list(...)
    # sorted_channel = ...

    # await save_json_to_file(...) # Сохранение ответа для отладки

    processed_count = 0
    for search_item in search_items:
        video_id = search_item.get("id", {}).get("videoId")
        channel_id = search_item.get("snippet", {}).get("channelId")
        video_detail = video_details_map.get(video_id)

        if not video_id or not channel_id or not video_detail:
            print(f"Skipping item due to missing data: video_id={video_id}, channel_id={channel_id}, has_details={bool(video_detail)}")
            continue # Пропускаем, если нет ID или деталей

        # Проверяем, не шортс ли это (если мы ищем обычные видео)
        if is_shorts_v(video_detail): # is_shorts_v ожидает объект video_detail
            print(f"Skipping video {video_id} as it's identified as shorts.")
            continue

        # --- ИЗМЕНЕНИЕ: Передаем youtube и channel_id в build_search_item_obj ---
        built_item = await build_search_item_obj(
            youtube,
            search_item, # Результат поиска (для возможного использования в будущем)
            video_detail, # Детали видео
            channel_id, # ID канала
        )

        if built_item:
            videos_result.append(built_item)
            processed_count += 1
            if len(videos_result) >= max_results: # Проверяем здесь, чтобы не обрабатывать лишнего
                 print(f"Reached max_results ({max_results}). Stopping processing.")
                 break # Достаточно результатов

    print(f"Processed {processed_count} items from this page. Total results in list: {len(videos_result)}")

    # Возвращаем обновленный список, токен следующей страницы и общее количество
    return videos_result, next_page_token_from_api, total_results


@router.get("/videos", response_model=SearchResponse)
async def search_videos(
    response: Response,
    query: str = Query(..., description="Поисковый запрос (название видео)"),
    max_results: int = Query(50, description="Количество видео в ответе", ge=1, le=50),
    date_published_filter: str = Query('all_time', alias="date_published", description="Дата публикации (all_time, last_week, last_month, last_3_month, last_6_month, last_year)"),
    # --- ИЗМЕНЕНИЕ: Добавляем зависимость для получения клиента YouTube ---
    youtube: build = Depends(get_user_youtube_client_via_cookie)
):
    """
    Эндпоинт для поиска видео с фильтрацией. Требует аутентификации.
    """
    print(f"Received video search request: query='{query}', max_results={max_results}, date='{date_published_filter}'")
    if date_published_filter not in ('all_time', 'last_week', 'last_month', 'last_3_month', 'last_6_month', 'last_year'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid value for date_published')

    encoded_query = quote(query, safe="") # Кодируем запрос
    rfc3339_date = get_rfc3339_date(date_published_filter) if date_published_filter != 'all_time' else None
    videos_result = []
    next_page_token = None
    total_results_estimate = 0 # Примерное общее количество
    max_pages_to_fetch = 5 # Ограничение на количество страниц для предотвращения долгого выполнения

    try:
        for page_num in range(max_pages_to_fetch):
            print(f"Fetching page {page_num + 1}...")
            videos_result, next_page_token, total_results_estimate = await get_videos(
                youtube=youtube,
                response=response,
                encoded_query=encoded_query,
                max_results=max_results, # Передаем желаемое конечное количество
                date_published=rfc3339_date,
                videos_result=videos_result, # Передаем текущий список для дополнения
                page_token=next_page_token
            )

            print(f"After page {page_num + 1}: {len(videos_result)} results collected. Next page token: {next_page_token}")

            # Проверяем, достигли ли мы нужного количества или закончились страницы
            if len(videos_result) >= max_results or not next_page_token:
                print("Stopping pagination: reached max results or no more pages.")
                break
        else:
             print(f"Stopped pagination after reaching max pages ({max_pages_to_fetch}).")


        final_results = videos_result[:max_results] # Обрезаем до нужного количества
        print(f"Returning {len(final_results)} video results.")

        return SearchResponse.model_validate({
            'item_count': len(final_results),
            'type': 'videos',
            'items': final_results,
        }).model_dump()

    except HTTPException as he:
         # Пробрасываем ошибки HTTP, которые могли возникнуть в get_videos
         raise he
    except Exception as e:
        print(f"Unexpected error in search_videos endpoint: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error during video search: {e}")

# --- ИЗМЕНЕНИЕ: Аналогично для search_shorts ---
@router.get("/shorts", response_model=SearchResponse)
async def search_shorts(
    response: Response,
    query: str = Query(..., description="Поисковый запрос (название шортсов)"),
    max_results: int = Query(50, description="Количество видео в ответе", ge=1, le=50),
    date_published_filter: str = Query('all_time', alias="date_published", description="Дата публикации (all_time, last_week, last_month, last_3_month, last_6_month, last_year)"),
    # --- ИЗМЕНЕНИЕ: Добавляем зависимость ---
    youtube: build = Depends(get_user_youtube_client_via_cookie)
):
    """
    Эндпоинт для поиска shorts с фильтрацией. Требует аутентификации.
    """
    print(f"Received shorts search request: query='{query}', max_results={max_results}, date='{date_published_filter}'")
    if date_published_filter not in ('all_time', 'last_week', 'last_month', 'last_3_month', 'last_6_month', 'last_year'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid value for date_published')

    encoded_query = quote(query, safe="")
    rfc3339_date = get_rfc3339_date(date_published_filter) if date_published_filter != 'all_time' else None
    shorts_result = []
    next_page_token = None
    total_results_estimate = 0
    max_pages_to_fetch = 5 # Ограничение

    try:
        for page_num in range(max_pages_to_fetch):
             print(f"Fetching page {page_num + 1} for shorts...")
             try:
                 search_response_dict = youtube.search().list(
                     q=encoded_query,
                     part='snippet',
                     type='video',
                     videoDuration='short', # Ищем только короткие видео
                     pageToken=next_page_token,
                     publishedAfter=rfc3339_date,
                     maxResults=50, # Запрашиваем больше для фильтрации
                 ).execute()
             except Exception as e:
                 print(f"Error during youtube.search().list for shorts: {e}")
                 if 'HttpError 403' in str(e) and 'quotaExceeded' in str(e):
                     raise HTTPException(status_code=429, detail="YouTube API quota exceeded for user.")
                 elif 'HttpError 401' in str(e) or 'HttpError 403' in str(e):
                     raise HTTPException(status_code=401, detail="YouTube API authorization error. Please re-login.")
                 else:
                     raise HTTPException(status_code=500, detail=f"YouTube API search error: {e}")

             total_results_estimate = search_response_dict.get('pageInfo', {}).get('totalResults', 0)
             next_page_token = search_response_dict.get('nextPageToken')
             search_items = search_response_dict.get('items', [])

             print(f"Found {len(search_items)} potential shorts. Total results: {total_results_estimate}. Next page token: {next_page_token}")


             if not search_items:
                 print("No more potential shorts found.")
                 break # Прерываем пагинацию

             video_ids = [item["id"]["videoId"] for item in search_items if item.get("id", {}).get("videoId")]

             if not video_ids:
                 continue # Следующая страница, если нет ID

             try:
                 video_response = youtube.videos().list(
                     part="snippet,contentDetails,statistics",
                     id=','.join(video_ids),
                 ).execute()
                 video_items = video_response.get('items', [])
             except Exception as e:
                 print(f"Error during youtube.videos().list for shorts details: {e}")
                 continue # Пропускаем эту страницу при ошибке получения деталей

             video_details_map = {v['id']: v for v in video_items}

             processed_count_page = 0
             for search_item in search_items:
                 video_id = search_item.get("id", {}).get("videoId")
                 channel_id = search_item.get("snippet", {}).get("channelId")
                 video_detail = video_details_map.get(video_id)

                 if not video_id or not channel_id or not video_detail:
                     continue

                 # --- ИЗМЕНЕНИЕ: Дополнительная проверка на is_shorts ---
                 # is_shorts проверяет еще и по тегам/длительности
                 if not is_shorts(video_detail):
                      print(f"Skipping video {video_id} as it's not identified as shorts by duration/tags.")
                      continue

                 built_item = await build_search_item_obj(
                     youtube,
                     search_item,
                     video_detail,
                     channel_id,
                     item_type='shorts', # Указываем тип
                 )

                 if built_item:
                     shorts_result.append(built_item)
                     processed_count_page += 1
                     if len(shorts_result) >= max_results:
                         break # Достаточно результатов

             print(f"Processed {processed_count_page} shorts from this page. Total collected: {len(shorts_result)}")

             # Проверяем выход из обоих циклов (внутреннего и внешнего)
             if len(shorts_result) >= max_results or not next_page_token:
                 print("Stopping shorts pagination: reached max results or no more pages.")
                 break # Выход из цикла пагинации
        else:
             print(f"Stopped shorts pagination after reaching max pages ({max_pages_to_fetch}).")


        final_results = shorts_result[:max_results]
        print(f"Returning {len(final_results)} shorts results.")

        return SearchResponse.model_validate({
            'item_count': len(final_results),
            'type': 'shorts',
            'items': final_results,
        }).model_dump()

    except HTTPException as he:
         raise he
    except Exception as e:
        print(f"Unexpected error in search_shorts endpoint: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error during shorts search: {e}")