# app/schemas/collection.py
from typing import List

from pydantic import BaseModel, HttpUrl
from datetime import datetime
import uuid

class CollectionBase(BaseModel):
    collection_title: str
    videos_urls: List[str]

class CollectionCreate(CollectionBase):
    pass  # Для создания достаточно базовой информации

class CollectionRead(CollectionBase):
    id: int
    user_id: uuid.UUID
    videos_urls: List[str]
    added_at: datetime

    class Config:
        from_attributes = True

class CollectionList(BaseModel): # Для вывода списка
    collections: list[CollectionRead]