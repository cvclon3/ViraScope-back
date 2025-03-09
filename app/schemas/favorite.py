# app/schemas/favorite.py

import uuid
from pydantic import BaseModel, HttpUrl
from datetime import datetime
from typing import Optional

class FavoriteChannelBase(BaseModel):
    channel_id: str
    channel_title: str
    channel_thumbnail: str  # URL
    channel_subscribers: int
    channel_video_count: int
    channel_last_published_at: datetime
    channel_url: str

class FavoriteChannelCreate(FavoriteChannelBase):
    pass  # Для создания достаточно базовой информации

class FavoriteChannelRead(FavoriteChannelBase):
    id: int
    user_id: uuid.UUID

    class Config:
        from_attributes = True

class FavoriteChannelList(BaseModel): # Для вывода списка
    channels: list[FavoriteChannelRead]