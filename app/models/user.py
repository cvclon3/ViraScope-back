# app/models/user.py
from typing import Optional
import uuid
from sqlmodel import Field, SQLModel

class User(SQLModel, table=True):
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4, primary_key=True, index=True, nullable=False
    )
    username: str = Field(index=True, unique=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str = Field()
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    # Другие поля (first_name, last_name, etc.)
    class Config:
        orm_mode = True