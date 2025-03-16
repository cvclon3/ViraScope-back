from fastapi import APIRouter, Query, HTTPException, Response, status
from app.models.search_models import Item, SearchResponse
from app.core.youtube import get_youtube_client, parse_duration
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


def build_search_item_obj(search_r, video_r, channel_r, item_type='video'):
    try:
        likes = int(video_r['statistics']['likeCount']) if 'likeCount' in video_r['statistics'] else 0
        likes_hidden = 'likeCount' not in video_r['statistics']

        comments = int(video_r['statistics']['commentCount']) if 'commentCount' in video_r['statistics'] else 0
        comments_hidden = 'commentCount' not in video_r['statistics']

        avg_views_per_video = float(channel_r['statistics'].get('viewCount', 0))/float(channel_r['statistics'].get('videoCount', -1))
        if avg_views_per_video <= 0:
            avg_views_per_video = video_r['statistics']['viewCount']
        combined_metric = float(video_r['statistics']['viewCount'])/avg_views_per_video

        if item_type == 'video':
            video_url = f'https://www.youtube.com/watch?v={video_r['id']}'
        elif item_type == 'shorts':
            video_url = f'https://www.youtube.com/shorts/{video_r["id"]}'
        else:
            video_url = f'https://www.youtube.com/watch?v={video_r["id"]}'

        search_item = Item.model_validate({
            'video_id': video_r['id'],
            'title': video_r['snippet']['title'],
            'thumbnail': video_r['snippet']['thumbnails']['high']['url'],
            'published_at': video_r['snippet']['publishedAt'],
            'views': int(video_r['statistics']['viewCount']),
            'channel_title': channel_r['snippet']['title'],
            'channel_url': f'https://www.youtube.com/channel/{channel_r['id']}',
            'channel_subscribers': int(channel_r['statistics']['subscriberCount']),
            'likes': likes,
            'likes_hidden': likes_hidden,
            'comments': comments,
            'comments_hidden': comments_hidden,
            'combined_metric': combined_metric,
            'duration': parse_duration(video_r['contentDetails']['duration']),
            'video_url': video_url,
            'channel_thumbnail': channel_r['snippet']['thumbnails']['high']['url'],
        })

        return search_item.model_dump()

    except Exception as e:
        print(f"Error in build_search_item_obj for video ID {video_r['id']}: {e}")
        return None


def is_shorts(video_r):
    # Проверяем наличие #Shorts в названии или описании
    title = video_r["snippet"]["title"]
    description = video_r["snippet"]["description"]
    duration = parse_duration(video_r['contentDetails']['duration'])
    return "#shorts" in title.lower() or "#shorts" in description.lower() or duration <= 3*60


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


@router.get("/videos", response_model=SearchResponse)
async def search_videos(
    response: Response,
    query: str = Query(..., description="Поисковый запрос (название видео)"),
    max_results: int = Query(50, description="Количество видео в ответе", ge=1, le=50),
):
    """
    Эндпоинт для поиска видео с фильтрацией.
    """
    try:
        encoded_query = quote(query, safe="-|")
        youtube = get_youtube_client()

        search_response_medium = youtube.search().list(
            q=encoded_query,
            part='snippet',
            type='video',
            videoDuration='medium',
            maxResults=25,
        ).execute()

        search_response_long = youtube.search().list(
            q=encoded_query,
            part='snippet',
            type='video',
            videoDuration='long',
            maxResults=25,
        ).execute()

        search_response = search_response_medium['items'] + search_response_long['items']

        if len(search_response) == 0:
            response.status_code = status.HTTP_406_NOT_ACCEPTABLE
            return SearchResponse.model_validate({
                'item_count': 0,
                'type': 'video',
                'items': [],
            }).model_dump()


        video_ids = [item["id"]["videoId"] for item in search_response]
        channel_ids = [item["snippet"]["channelId"] for item in search_response]

        video_response = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=','.join(video_ids),
        ).execute()

        sorted_video = sort_json_by_key_values(video_response['items'], video_ids, 'id')

        channel_response = youtube.channels().list(
            part="snippet,contentDetails,statistics",
            id=','.join(channel_ids),
        ).execute()

        sorted_channel = sort_json_by_key_values(channel_response['items'], channel_ids, 'id')

        await save_json_to_file({
            'search_medium': search_response_medium,
            'search_long': search_response_long,
            'videos': sorted_video,
            'channels': sorted_channel,
        })

        videos_result = []
        for i in range(min(max_results, len(search_response))):
            channel_ = find_object_with_next(sorted_channel, 'id', sorted_video[i]['snippet']['channelId'])

            search_item = build_search_item_obj(
                search_response[i],
                sorted_video[i],
                channel_,
            )

            # if (search_response['items'][i]['id']['videoId'] != sorted_video[i]['id']) or (search_response['items'][i]['snippet']['channelId'] != channel_['id']):
            #     print('FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF')

            videos_result.append(search_item)

        return SearchResponse.model_validate({
            'item_count': len(videos_result),
            'type': 'videos',
            'items': videos_result,
        }).model_dump()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shorts", response_model=SearchResponse)
async def search_shorts(
    response: Response,
    query: str = Query(..., description="Поисковый запрос (название шортсов)"),
    max_results: int = Query(50, description="Количество видео в ответе", ge=1, le=50),
):
    """
    Эндпоинт для поиска видео с фильтрацией.
    """
    # try:
    encoded_query = quote(query, safe="-|")
    youtube = get_youtube_client()

    search_response_short = youtube.search().list(
        q=encoded_query,
        part='snippet',
        type='video',
        videoDuration='short',
        maxResults=50,
    ).execute()

    search_response = search_response_short['items']

    if len(search_response) == 0:
        response.status_code = status.HTTP_406_NOT_ACCEPTABLE
        return SearchResponse.model_validate({
            'item_count': 0,
            'type': 'video',
            'items': [],
        }).model_dump()

    video_ids = [item["id"]["videoId"] for item in search_response]
    channel_ids = [item["snippet"]["channelId"] for item in search_response]

    video_response = youtube.videos().list(
        part="snippet,contentDetails,statistics",
        id=','.join(video_ids),
    ).execute()

    sorted_video = sort_json_by_key_values(video_response['items'], video_ids, 'id')

    channel_response = youtube.channels().list(
        part="snippet,contentDetails,statistics",
        id=','.join(channel_ids),
    ).execute()

    sorted_channel = sort_json_by_key_values(channel_response['items'], channel_ids, 'id')

    await save_json_to_file({
        'search_short': search_response_short,
        'videos': sorted_video,
        'channels': sorted_channel,
    })

    shorts_result = []
    for i in range(min(max_results, len(search_response))):
        if not is_shorts(sorted_video[i]):
            continue

        channel_ = find_object_with_next(sorted_channel, 'id', sorted_video[i]['snippet']['channelId'])

        search_item = build_search_item_obj(
            search_response[i],
            sorted_video[i],
            channel_,
            item_type='shorts',
        )

        # if (search_response['items'][i]['id']['videoId'] != sorted_video[i]['id']) or (search_response['items'][i]['snippet']['channelId'] != channel_['id']):
        #     print('FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF')

        shorts_result.append(search_item)

    return SearchResponse.model_validate({
        'item_count': len(search_response),
        'type': 'shorts',
        'items': shorts_result,
    }).model_dump()

    # except Exception as e:
    #     raise HTTPException(status_code=500, detail=str(e))
