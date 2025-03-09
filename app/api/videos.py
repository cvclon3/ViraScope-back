from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional, Dict
from app.core.youtube import get_youtube_client, get_recent_views, get_total_videos_on_channel
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

        likes = int(statistics['likeCount']) if 'likeCount' in statistics else 0
        likes_hidden = 'likeCount' not in statistics
        views = int(statistics['viewCount'])
        subscribers = int(channel_info['subscribers'])
        comments = int(statistics['commentCount']) if 'commentCount' in statistics else 0
        comments_hidden = 'commentCount' not in statistics
        duration = parse_duration(content_details['duration'])
        vps = views / subscribers if subscribers > 0 else None
        lpv = likes / views if views > 0 and not likes_hidden else None
        cpv = comments / views if views > 0 and not comments_hidden else None
        vps_log = math.log(vps + 1) if vps is not None else 0
        vps_norm = min(vps_log / math.log(11), 1) if vps_log > 0 else 0
        lpv_norm = min(lpv / 0.1, 1) if lpv is not None else 0
        cpv_norm = min(cpv / 0.01, 1) if cpv is not None else 0
        w1, w2, w3 = 2, 1, 1
        combined_metric = (w1 * vps_norm + w2 * lpv_norm + w3 * cpv_norm) / (w1 + w2 + w3) * 100 if (
                    vps_norm + lpv_norm + cpv_norm) > 0 else None

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
            'views_per_subscriber': vps,
            'likes_per_view': lpv,
            'comments': comments,
            'comments_per_view': cpv,
            'combined_metric': combined_metric,
            'duration': duration,
            'total_videos': total_videos,
            'video_url': f'https://www.youtube.com/watch?v={video_id}',  # Добавляем URL
        })

        return video_info.dict()

    except Exception as e:
        print(f"Error in get_video_info for video ID {video_id}: {e}")
        return None
async def get_channel_info(channel_id: str) -> Optional[Dict]:
    """
    Получает информацию о канале
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
        return {
            'subscribers': int(channel_stats['subscriberCount']) if 'subscriberCount' in channel_stats else 0,
        }
    except Exception as e:
        print(f"Error in get_channel_info for {channel_id=}: {e}")
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
):
    """
    Эндпоинт для поиска видео с фильтрацией.
    """
    try:
        youtube = get_youtube_client()

        encoded_query = quote_plus(query)

        search_response = youtube.search().list(
            q=encoded_query,
            part='snippet',
            type='video',
            maxResults=max_results
        ).execute()

        videos = []
        for search_result in search_response.get('items', []):
            video_id = search_result['id']['videoId']
            video_info = await get_video_info(video_id)
            if video_info:
                # Фильтрация
                if min_combined_metric is not None and (video_info['combined_metric'] is None or video_info['combined_metric'] < min_combined_metric):
                    continue
                if max_combined_metric is not None and video_info['combined_metric'] is not None and video_info['combined_metric'] > max_combined_metric:
                    continue
                if min_views is not None and video_info['views'] < min_views:
                    continue
                if max_views is not None and video_info['views'] > max_views:
                    continue
                if min_channel_subscribers is not None and video_info['channel_subscribers'] < min_channel_subscribers:
                    continue
                if max_channel_subscribers is not None and video_info['channel_subscribers'] > max_channel_subscribers:
                    continue

                if min_duration is not None and video_info['duration'] < min_duration:
                    continue

                if max_duration is not None and video_info['duration'] > max_duration:
                  continue

                if min_comments is not None and (video_info['comments'] is None or video_info['comments'] < min_comments):
                    continue
                if max_comments is not None and video_info['comments'] is not None and video_info['comments'] > max_comments:
                    continue

                if min_total_videos is not None and (video_info['total_videos'] is None or video_info['total_videos'] < min_total_videos):
                    continue
                if max_total_videos is not None and video_info['total_videos'] is not None and video_info['total_videos'] > max_total_videos:
                    continue

                if published_date:
                    published_at = video_info['published_at']
                    now = datetime.datetime.now(datetime.timezone.utc)
                    if published_date == "last_week":
                        if (now - published_at).days > 7:
                            continue
                    elif published_date == "last_month":
                        if (now - published_at).days > 30:
                            continue
                    elif published_date == "last_3_months":
                        if (now - published_at).days > 90:
                            continue
                    elif published_date == "last_6_months":
                        if (now - published_at).days > 180:
                            continue
                    elif published_date == "last_year":
                        if (now - published_at).days > 365:
                            continue
                videos.append(video_info)

        return videos

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))