# app/schemas/user.py
from typing import Optional
from pydantic import BaseModel, EmailStr, Field
import uuid

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr

class UserCreate(UserBase):
    password: str = Field(..., min_length=8)

class UserRead(UserBase):
    id: uuid.UUID
    is_active: bool
    is_superuser: bool  # Добавляем
    class Config:
        from_attributes = True

class UserUpdate(BaseModel):  # Наследуемся от BaseModel
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=8)