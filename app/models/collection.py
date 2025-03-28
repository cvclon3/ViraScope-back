import uuid

from pydantic import json
from sqlalchemy import JSON
from sqlmodel import SQLModel, Field, Relationship, UniqueConstraint
from typing import Optional, List
from datetime import datetime, timezone

class Collection(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")  # Внешний ключ на User
    collection_title: str = Field(index=True)
    videos_urls: str = Field(default="[]")  # Храним как JSON строку
    added_at: datetime = Field(default_factory=datetime.now)

    user: "User" = Relationship(back_populates="collections") #Связь с User

    __table_args__ = (
        UniqueConstraint("user_id", "collection_title"),
    )

from .user import User  # Импортируем User *после* определения FavoriteChannel,
                      # чтобы избежать циклического импорта.
User.model_rebuild() # нужно для обновления forward ref