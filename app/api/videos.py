from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional, Dict
from app.core.youtube import (get_youtube_client, get_recent_views,
                              get_total_videos_on_channel, get_channel_views, parse_duration)
from app.models.video import Video
import math
import datetime
import re
from urllib.parse import quote_plus

router = APIRouter()

async def get_video_info(video_id: str) -> Optional[Dict]:
    """
    Получает подробную информацию об одном видео по его ID.
    """
    try:
        youtube = get_youtube_client()
        video_response = youtube.videos().list(
            part='snippet,statistics,contentDetails',
            id=video_id
        ).execute()

        if not video_response['items']:
            return None

        video_data = video_response['items'][0]
        snippet = video_data['snippet']
        statistics = video_data['statistics']
        content_details = video_data['contentDetails']

        channel_id = snippet['channelId']
        channel_info = await get_channel_info(channel_id)
        if not channel_info:
            return None
        total_videos = get_total_videos_on_channel(channel_id)
        all_channel_views = await get_channel_views(channel_id)

        likes = int(statistics['likeCount']) if 'likeCount' in statistics else 0
        likes_hidden = 'likeCount' not in statistics
        views = int(statistics['viewCount'])
        subscribers = int(channel_info['subscribers'])
        comments = int(statistics['commentCount']) if 'commentCount' in statistics else 0
        comments_hidden = 'commentCount' not in statistics  # не используется
        duration = parse_duration(content_details['duration'])

        average_channel_views_per_video = all_channel_views / total_videos if total_videos and total_videos > 0 else None
        combined_metric = views / average_channel_views_per_video if average_channel_views_per_video is not None and average_channel_views_per_video > 0 else None

        video_info = Video.parse_obj({
            'video_id': video_id,
            'title': snippet['title'],
            'thumbnail': snippet['thumbnails']['high']['url'],
            'published_at': snippet['publishedAt'],
            'views': views,
            'channel_title': snippet['channelTitle'],
            'channel_url': f'https://www.youtube.com/channel/{channel_id}',
            'channel_subscribers': subscribers,
            'likes': likes,
            'likes_hidden': likes_hidden,
            'views_per_subscriber': None,
            'likes_per_view': None,
            'comments': comments,
            'comments_per_view': None,
            'combined_metric': combined_metric,
            'duration': duration,
            'total_videos': total_videos,
            'video_url': f'https://www.youtube.com/watch?v={video_id}',
        })

        return video_info.dict()

    except Exception as e:
        print(f"Error in get_video_info for video ID {video_id}: {e}")
        return None

async def get_channel_info(channel_id: str) -> Optional[Dict]:
    """Получает информацию о канале."""
    try:
        youtube = get_youtube_client()
        channel_response = youtube.channels().list(
            part="statistics",
            id=channel_id
        ).execute()
        if not channel_response["items"]:
            return None
        channel_stats = channel_response["items"][0]["statistics"]
        return {
            'subscribers': int(channel_stats['subscriberCount']) if 'subscriberCount' in channel_stats else 0,
        }
    except Exception as e:
        print(f"Error in get_channel_info for {channel_id=}: {e}")
        return None

async def get_channel_views(channel_id: str) -> Optional[int]:
    """Получает суммарное количество просмотров на канале."""
    try:
        youtube = get_youtube_client()
        channel_response = youtube.channels().list(
            part="statistics",
            id=channel_id
        ).execute()

        if not channel_response["items"]:
            return None
        channel_stats = channel_response["items"][0]["statistics"]
        return int(channel_stats['viewCount']) if 'viewCount' in channel_stats else 0

    except Exception as e:
        print(f"Error in get_channel_views for channel ID {channel_id}: {e}")
        return None

def parse_duration(duration_str: str) -> int:
    """Преобразует строку длительности видео в формате ISO 8601 в секунды."""
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str)

    if not match:
        return 0

    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0

    return hours * 3600 + minutes * 60 + seconds


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
):
    """
    Эндпоинт для поиска видео с фильтрацией.
    """
    try:
        youtube = get_youtube_client()
        encoded_query = quote_plus(query)

        videos = []
        next_page_token = None
        total_results = 0
        retrieved_count = 0
        max_iterations = 10  # Максимальное количество итераций
        iteration_count = 0
        max_execution_time = 30  # Секунды
        start_time = datetime.datetime.now()

        # Первый запрос для получения totalResults
        search_response = youtube.search().list(
            q=encoded_query,
            part='snippet',
            type='video',
            maxResults=50,  # Запрашиваем сразу 50
            # pageToken=next_page_token  #  НЕ указываем pageToken для ПЕРВОГО запроса!
        ).execute()

        total_results = search_response['pageInfo']['totalResults']
        items = search_response.get('items', [])  # Сразу обрабатываем
        next_page_token = search_response.get('nextPageToken')  # Получаем токен для СЛЕДУЮЩЕГО запроса

        while True:
            iteration_count += 1
            if iteration_count > max_iterations:
                print("Превышено максимальное количество итераций цикла while.")
                break
            if (datetime.datetime.now() - start_time).total_seconds() > max_execution_time:
                print("Превышено максимальное время выполнения цикла while.")
                break
            if max_results > total_results:
                print(f"Запрошенное количество видео ({max_results}) больше, чем всего найдено ({total_results}).")
                break

            if not items:  # Если нет результатов, то выходим
                break

            for search_result in items:
                video_id = search_result['id']['videoId']
                video_info = await get_video_info(video_id)
                if video_info:
                    # --- Фильтрация по типу видео (Shorts) ---
                    is_short = False
                    video_response = youtube.videos().list(
                        part='contentDetails',
                        id=video_id
                    ).execute()

                    if video_response['items']:
                        video_data = video_response['items'][0]
                        content_details = video_data.get('contentDetails')
                        if content_details:
                            content_rating = content_details.get('contentRating')
                            if content_rating and content_rating.get('ytRating') == 'ytAgeRestricted':
                                is_short = True
                            elif content_details.get('duration'):  # Доп проверка по длительности
                                duration = parse_duration(content_details['duration'])
                                if duration <= 60:
                                    is_short = True

                    should_add = True

                    if video_type == "video" and is_short:
                        should_add = False
                    elif video_type == "shorts" and not is_short:
                        should_add = False

                    # --- Остальная фильтрация ---
                    if min_combined_metric is not None and (
                            video_info['combined_metric'] is None or video_info['combined_metric'] < min_combined_metric):
                        should_add = False
                    if max_combined_metric is not None and video_info['combined_metric'] is not None and video_info[
                        'combined_metric'] > max_combined_metric:
                        should_add = False
                    if min_views is not None and video_info['views'] < min_views:
                        should_add = False
                    if max_views is not None and video_info['views'] > max_views:
                        should_add = False
                    if min_channel_subscribers is not None and video_info['channel_subscribers'] < min_channel_subscribers:
                        should_add = False
                    if max_channel_subscribers is not None and video_info['channel_subscribers'] > max_channel_subscribers:
                        should_add = False
                    if min_duration is not None and video_info['duration'] < min_duration:
                        should_add = False
                    if max_duration is not None and video_info['duration'] > max_duration:
                        should_add = False
                    if min_comments is not None and (
                            video_info['comments'] is None or video_info['comments'] < min_comments):
                        should_add = False
                    if max_comments is not None and video_info['comments'] is not None and video_info['comments'] > max_comments:
                        should_add = False
                    if min_total_videos is not None and (
                            video_info['total_videos'] is None or video_info['total_videos'] < min_total_videos):
                        should_add = False
                    if max_total_videos is not None and video_info['total_videos'] is not None and video_info[
                        'total_videos'] > max_total_videos:
                        should_add = False

                    if published_date:
                        published_at = video_info['published_at']
                        now = datetime.datetime.now(datetime.timezone.utc)
                        if published_date == "last_week":
                            if (now - published_at).days > 7:
                                should_add = False
                        elif published_date == "last_month":
                            if (now - published_at).days > 30:
                                should_add = False
                        elif published_date == "last_3_months":
                            if (now - published_at).days > 90:
                                should_add = False
                        elif published_date == "last_6_months":
                            if (now - published_at).days > 180:
                                should_add = False
                        elif published_date == "last_year":
                            if (now - published_at).days > 365:
                                should_add = False

                    if should_add:
                        videos.append(video_info)
                        retrieved_count += 1

                if retrieved_count >= max_results:
                    break

            if retrieved_count >= max_results:  # Выходим, если набрали нужное количество
                break

            #  Запрашиваем СЛЕДУЮЩУЮ страницу, ТОЛЬКО если есть next_page_token
            if next_page_token:
                search_response = youtube.search().list(
                    q=encoded_query,
                    part='snippet',
                    type='video',
                    maxResults=50,
                    pageToken=next_page_token  # !!! Используем токен
                ).execute()
                items = search_response.get('items', [])
                next_page_token = search_response.get('nextPageToken')  # Обновляем токен
            else:
                break  # Выходим, если нет следующей страницы

        return videos[:max_results]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))