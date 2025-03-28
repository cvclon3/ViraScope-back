# app/schemas/collection.py
from typing import List

from pydantic import BaseModel, HttpUrl, Field
from datetime import datetime
import uuid
import json

class CollectionBase(BaseModel):
    collection_title: str
    videos_urls: str = Field(default="[]")  # Храним как JSON строку

    @property
    def videos_list(self) -> List[str]:
        return json.loads(self.videos_urls)

    @videos_list.setter
    def videos_list(self, value: List[str]):
        self.videos_urls = json.dumps(value)

class CollectionCreate(CollectionBase):
    videos_urls: List[str]  # При создании принимаем список

    def prepare_for_db(self):
        # Конвертируем список в JSON строку перед сохранением
        return {
            "collection_title": self.collection_title,
            "videos_urls": json.dumps(self.videos_urls)
        }

class CollectionRead(BaseModel):
    id: int
    user_id: uuid.UUID
    collection_title: str
    videos_urls: List[str]  # При чтении возвращаем как список
    added_at: datetime

    @classmethod
    def from_db(cls, db_model):
        # Конвертируем JSON строку обратно в список при чтении из БД
        # TODO: дописать конвертацию URL в видео запросами к ютуб АПИ
        return cls(
            id=db_model.id,
            user_id=db_model.user_id,
            collection_title=db_model.collection_title,
            videos_urls=json.loads(db_model.videos_urls),
            added_at=db_model.added_at
        )

    class Config:
        from_attributes = True

class CollectionList(BaseModel):
    collections: List[CollectionRead]

    @classmethod
    def from_db(cls, db_models):
        return cls(
            collections=[CollectionRead.from_db(model) for model in db_models]
        )