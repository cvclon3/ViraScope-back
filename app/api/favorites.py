# app/api/favorites.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List
import re
import datetime

from app.core.database import get_db
from app.models.user import User
from app.models.favorite import FavoriteChannel
from app.schemas.favorite import FavoriteChannelCreate, FavoriteChannelRead, FavoriteChannelList
from app.api.users import get_current_active_user  # Используем нашу зависимость
from app.core.youtube import get_youtube_client, get_total_videos_on_channel, get_channel_info

router = APIRouter()

def extract_channel_id(url: str) -> str | None:
    """Извлекает ID канала из URL."""
    # https://www.youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ
    # https://www.youtube.com/user/my_user_name
    # https://www.youtube.com/c/MyChannelName
    match = re.search(r"youtube\.com/(?:channel/|user/|c/)([^/?]+)", url) #Добавил /c/, так как иногда /c/ используется
    if match:
        return match.group(1)
    return None

@router.post("/favorites/", response_model=List[FavoriteChannelRead], status_code=201)
async def add_favorite_channels(
    channel_urls: List[str],  # Принимаем список URL
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Добавляет каналы в избранное пользователя."""
    added_channels = []
    for url in channel_urls:
        channel_id = extract_channel_id(url)
        if not channel_id:
            # Можно добавить более детальную обработку ошибок (какой URL невалиден)
            raise HTTPException(status_code=400, detail=f"Invalid channel URL: {url}")

        # Проверяем, есть ли уже такой канал у этого пользователя
        existing_channel = db.exec(
            select(FavoriteChannel)
            .where(FavoriteChannel.user_id == current_user.id)
            .where(FavoriteChannel.channel_id == channel_id)
        ).first()
        if existing_channel:
            continue  # Уже в избранном, пропускаем

        # Получаем информацию о канале с YouTube
        youtube = get_youtube_client()
        try:
            channel_info_dict = await get_channel_info(channel_id) #get_channel_info - уже есть
            if not channel_info_dict:
              raise Exception("Channel not found")
            video_count = get_total_videos_on_channel(channel_id) #get_total_videos_on_channel тоже есть
            if not video_count:
              raise Exception("Can't get video count")

            # Ищем последнее видео на канале, чтобы узнать дату публикации
            search_response = youtube.search().list(
                part='snippet',
                channelId=channel_id,
                type='video',
                order='date',
                maxResults=1
            ).execute()

            last_published_at = None
            if search_response['items']:
                last_published_at = datetime.datetime.fromisoformat(search_response['items'][0]['snippet']['publishedAt'].replace('Z', '+00:00'))

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting channel info for {channel_id}: {e}")


        # Создаем объект FavoriteChannel
        favorite_channel = FavoriteChannel(
            user_id=current_user.id,
            channel_id=channel_id,
            channel_title=channel_info_dict['channel_title'],
            channel_thumbnail=channel_info_dict['channel_thumbnail'],
            channel_subscribers=channel_info_dict['channel_subscribers'],
            channel_video_count=video_count,
            channel_last_published_at=last_published_at,
            channel_url=channel_info_dict['channel_url'],

        )
        db.add(favorite_channel)
        added_channels.append(favorite_channel)

    db.commit()
    # db.refresh(added_channels)  # refresh для списка не работает, делаем ниже
    return [FavoriteChannelRead.model_validate(channel) for channel in added_channels] #возвращаем FavoriteChannelRead

@router.get("/favorites/", response_model=FavoriteChannelList)
async def get_favorite_channels(current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    """Возвращает список избранных каналов пользователя."""
    channels = db.exec(select(FavoriteChannel).where(FavoriteChannel.user_id == current_user.id)).all()
    return {"channels": channels}

@router.delete("/favorites/{channel_id}", status_code=204)
async def delete_favorite_channel(
    channel_id: str, current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """Удаляет канал из избранного пользователя."""

    channel = db.exec(
        select(FavoriteChannel)
        .where(FavoriteChannel.user_id == current_user.id)
        .where(FavoriteChannel.channel_id == channel_id)
    ).first()

    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found in favorites")

    db.delete(channel)
    db.commit()
    return {"message": "Channel deleted from favorites"}