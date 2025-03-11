# app/models/user.py
from typing import Optional, List
import uuid
from sqlmodel import Field, SQLModel, Relationship


class User(SQLModel, table=True):  # Наследуемся ТОЛЬКО от SQLModel
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4, primary_key=True, index=True, nullable=False
    )
    username: str = Field(index=True, unique=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str = Field()
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    favorite_channels: List["FavoriteChannel"] = Relationship(back_populates="user")

    # Другие поля
    class Config:
        from_attributes = True

from .favorite import FavoriteChannel #  внизу, чтобы избежать циклического импорта