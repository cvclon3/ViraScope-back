# app/models/search_models.py
from pydantic import BaseModel, HttpUrl, Field
from datetime import datetime
from typing import Optional


class Item(BaseModel):
    video_id: str
    title: str
    thumbnail: HttpUrl
    published_at: datetime
    views: int
    channel_title: str
    channel_url: HttpUrl
    channel_subscribers: int
    likes: Optional[int] = Field(None, description="Количество лайков")
    likes_hidden: bool = Field(False, description="Скрыта ли статистика лайков")
    comments: Optional[int] = Field(None, description="Количество комментариев")
    comments_hidden: bool = Field(False, description="Скрыта ли статистика лайков")
    combined_metric: Optional[float] = Field(None, description="Комбинированная метрика")
    duration: int = Field(..., description="Длительность видео в секундах")
    video_url: HttpUrl = Field(..., description="Ссылка на видео")
    channel_thumbnail: HttpUrl

    class Config:
        orm_mode = True


class SearchResponse(BaseModel):
    item_count: int = Field(description="Количество видео/шортсов")
    type: str = Field(description="Видео/шортсы")
    items: list[Item]

    class Config:
        orm_mode = True
