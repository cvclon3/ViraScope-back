from fastapi import APIRouter, Query, HTTPException, Response, status
from app.core.youtube import get_youtube_client
from urllib.parse import quote
from typing import Optional, List


router = APIRouter()


@router.get("/getcomments")
async def get_comments(
        video_ids: str = Query(..., description="ID видео для которого необходимо получить комментарии"),
):
    try:
        youtube = get_youtube_client()

        comments = youtube.commentThreads().list(
            part='snippet,replies',
            videoId=video_ids,
            maxResults=100,
            order='relevance',
            textFormat='plainText',
        ).execute()

        return comments

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))