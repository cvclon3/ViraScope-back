# app/models/favorite.py

import uuid
from sqlmodel import SQLModel, Field, Relationship, UniqueConstraint
from typing import Optional
from datetime import datetime

class FavoriteChannel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")  # Внешний ключ на User
    channel_id: str = Field(index=True)  # ID канала на YouTube
    channel_title: str
    channel_thumbnail: str  # URL логотипа
    channel_subscribers: int
    channel_video_count: int
    channel_last_published_at: datetime
    channel_url: str

    user: "User" = Relationship(back_populates="favorite_channels") #Связь с User

    #Добавляем составной индекс, чтобы не хранить дубли
    __table_args__ = (
        UniqueConstraint("user_id", "channel_id"),
    )

from .user import User  # Импортируем User *после* определения FavoriteChannel,
                      # чтобы избежать циклического импорта.
User.model_rebuild() # нужно для обновления forward ref