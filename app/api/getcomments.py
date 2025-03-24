from fastapi import APIRouter, Query, HTTPException, Response, status
from app.core.youtube import get_youtube_client
from urllib.parse import quote
from typing import Optional, List
from math import ceil
import json
import uuid
from pathlib import Path
import aiofiles


router = APIRouter()


async def save_json_to_file(data):
    # Преобразуем словарь в JSON-строку
    json_data = json.dumps(data, indent=4)

    # Генерируем уникальный идентификатор
    unique_id = str(uuid.uuid4())

    # Создаем путь к папке data
    data_dir = Path("comms")
    data_dir.mkdir(exist_ok=True)  # Создаем папку, если её нет

    # Создаем имя файла
    file_name = data_dir / f"comms_{unique_id}.json"

    # Асинхронно записываем данные в файл
    async with aiofiles.open(file_name, mode='w') as json_file:
        await json_file.write(json_data)


@router.get("/getcomments")
async def get_comments(
    video_id: str = Query(..., description="ID видео для которого необходимо получить комментарии"),
):
    try:
        youtube = get_youtube_client()

        video_info = youtube.videos().list(
            part="statistics",
            id=video_id,
        ).execute()

        if 'commentCount' not in video_info['items'][0]['statistics']:
            return {'detail': 'comments hidden'}

        comments_count = int(video_info['items'][0]['statistics']['commentCount'])

        if comments_count == 0:
            return {'detail': 'no comments'}

        try:
            comments_response = youtube.commentThreads().list(
                part='snippet,replies',
                videoId=video_id,
                maxResults=100,
                order='relevance',
                textFormat='plainText',
            ).execute()
        except Exception as e:
            raise HTTPException(status_code=403, detail='comments disabled')

        await save_json_to_file({
            'video_id': video_id,
            'comments': comments_response,
        })

        comments_response = comments_response['items']
        comments_results = [comm['snippet']['topLevelComment']['snippet']['textOriginal'] for comm in comments_response]

        return {'comments_count': len(comments_results), 'items': comments_results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# @router.get("/getcomments")
# async def get_comments(
#     video_id: str = Query(..., description="ID видео для которого необходимо получить комментарии"),
# ):
#     # try:
#     # percentage_get_comments = 10
#
#     youtube = get_youtube_client()
#     # comments_result = []
#
#     video_info = youtube.videos().list(
#         part="statistics",
#         id=video_id,
#     ).execute()
#
#     if 'commentCount' not in video_info['items'][0]['statistics']:
#         return {'detail': 'comments hidden'}
#
#     comments_count = int(video_info['items'][0]['statistics']['commentCount'])
#
#     if comments_count == 0:
#         return {'detail': 'no comments'}
#
#     # comments_get_count = min(int(comments_count * percentage_get_comments / 100), 1000)
#     # pages_count = ceil(comments_get_count / 100)
#
#     try:
#         comments_response = youtube.commentThreads().list(
#             part='snippet,replies',
#             videoId=video_id,
#             maxResults=100,
#             order='relevance',
#             textFormat='plainText',
#         ).execute()
#     except Exception as e:
#         raise HTTPException(status_code=403, detail='comments disabled')
#
#     await save_json_to_file({
#         'video_id': video_id,
#         'comments': comments_response,
#     })
#
#     comments_response = comments_response['items']
#     comments_results = [comm['snippet']['topLevelComment']['snippet']['textOriginal'] for comm in comments_response]
#
#
#     # next_page_token = None
#
#     # for i in range(pages_count):
#     #     if not next_page_token:
#     #         comments_responce_page = youtube.commentThreads().list(
#     #             part='snippet,replies',
#     #             videoId=video_id,
#     #             maxResults=100,
#     #             order='relevance',
#     #             textFormat='plainText',
#     #         ).execute()
#     #     else:
#     #         comments_responce_page = youtube.commentThreads().list(
#     #             part='snippet,replies',
#     #             videoId=video_id,
#     #             maxResults=100,
#     #             order='relevance',
#     #             pageToken=next_page_token,
#     #             textFormat='plainText',
#     #         ).execute()
#     #
#     #     next_page_token = comments_responce_page['nextPageToken'] if 'nextPageToken' in comments_responce_page else -1
#     #     comments_responce += comments_responce_page['items']
#
#     # comments = youtube.commentThreads().list(
#     #     part='snippet,replies',
#     #     videoId=video_id,
#     #     maxResults=100,
#     #     order='relevance',
#     #     textFormat='plainText',
#     # ).execute()
#
#     # if comments_count - len(comments['items']) > 0:
#
#
#     return {'comments_count': len(comments_results), 'items': comments_results}
#
#     # except Exception as e:
#     #     raise HTTPException(status_code=500, detail=str(e))