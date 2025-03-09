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
    views_per_subscriber: Optional[float] = Field(None, description="Отношение просмотров к подписчикам")
    likes_per_view: Optional[float] = Field(None, description="Отношение лайков к просмотрам")
    comments: Optional[int] = Field(None, description="Количество комментариев")
    comments_per_view: Optional[float] = Field(None, description="Отношение комментариев к просмотрам")
    combined_metric: Optional[float] = Field(None, description="Комбинированная метрика")
    duration: int
    total_videos: Optional[int] = None
    video_url: HttpUrl = Field(..., description="Ссылка на видео")  # Добавляем поле video_url

    @validator("published_at", pre=True)
    def parse_published_at(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value

    class Config:
        orm_mode = True