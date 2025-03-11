# app/models/video.py
from pydantic import BaseModel, HttpUrl, validator, Field
from datetime import datetime
from typing import Optional

class Video(BaseModel):
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
    combined_metric: Optional[float] = Field(None, description="Комбинированная метрика")
    duration: int = Field(..., description="Длительность видео в секундах")
    total_videos: Optional[int] = Field(None, description="Общее количество видео на канале")
    video_url: HttpUrl = Field(..., description="Ссылка на видео")


    @validator("published_at", pre=True)
    def parse_published_at(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value

    class Config:
        orm_mode = True