from pydantic import BaseModel, HttpUrl, validator
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

    @validator("published_at", pre=True)
    def parse_published_at(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value
    class Config:
      orm_mode = True