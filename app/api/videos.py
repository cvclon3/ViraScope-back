from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional, Dict
from app.core.youtube import get_youtube_client, get_recent_views  # get_recent_views пока не используем
from app.models.video import Video

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

        channel_id = snippet['channelId']
        channel_info = await get_channel_info(channel_id)
        if not channel_info:
            return None
        # Явное преобразование в int перед использованием
        views = int(statistics['viewCount'])
        subscribers = int(channel_info['subscribers'])  #Уже преобразовывали, но лучше перестраховаться
        likes = int(statistics['likeCount']) if 'likeCount' in statistics else 0 #Также добавляем значение по умолчанию

        video_info = Video.parse_obj({
            'video_id': video_id,
            'title': snippet['title'],
            'thumbnail': snippet['thumbnails']['high']['url'],
            'published_at': snippet['publishedAt'],
            'views': views, # Используем преобразованные значения
            'channel_title': snippet['channelTitle'],
            'channel_url': f'https://www.youtube.com/channel/{channel_id}',
            'channel_subscribers': subscribers, # Используем преобразованные значения
            'likes': likes,
            'views_per_subscriber':  views / subscribers if subscribers > 0 else None,  # Используем переменные
            'likes_per_view': likes / views if views > 0 else None,  # Используем переменные

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

@router.get("/", response_model=List[Video])
async def get_videos_by_title(
    query: str = Query(..., description="Поисковый запрос (название видео)"),
    max_results: int = Query(10, description="Максимальное количество результатов", ge=1, le=50)
):
    """
    Эндпоинт для поиска видео.
    """
    try:
        youtube = get_youtube_client()
        search_response = youtube.search().list(
            q=query,
            part='snippet',
            type='video',
            maxResults=max_results
        ).execute()

        videos = []
        for search_result in search_response.get('items', []):
            video_id = search_result['id']['videoId']
            video_info = await get_video_info(video_id)
            if video_info:
                videos.append(video_info)

        return videos

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))