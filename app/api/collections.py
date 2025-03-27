# app/api/collections.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.database import get_db
from app.models.user import User
from app.models.collection import Collection
from app.schemas.collection import CollectionList, CollectionRead, CollectionCreate
from app.api.auth import get_current_user  # Используем нашу зависимость


router = APIRouter()

@router.post("/", response_model=List[CollectionRead], status_code=201)
async def create_collection(
    collection_title: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создаёт новую коллекцию."""
    collection = Collection(user_id=current_user.id, collection_title=collection_title)
    db.add(collection)
    db.commit()
    db.refresh(collection)
    return [collection]

@router.get("/", response_model=CollectionList)
async def get_collections(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Возвращает список коллекций пользователя."""
    collections = db.exec(select(Collection).where(Collection.user_id == current_user.id)).all()
    return {"collections": collections}

@router.delete("/{collection_id}", status_code=204)
async def delete_favorite_channel(
    collection_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Удаляет коллекцию у пользователя."""

    collection = db.exec(
        select(Collection)
        .where(Collection.user_id == current_user.id)
        .where(Collection.id == collection_id)
    ).first()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    db.delete(collection)
    db.commit()
    return {"message": "Collection deleted"}

@router.post("/add/{collection_id}", response_model=CollectionRead, status_code=201)
async def add_favorite_channels(
    videos_urls: List[str],  # Принимаем список URL
    collection_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Добавляет видео в коллекцию пользователя."""
    collection = db.exec(
        select(Collection)
        .where(Collection.id == collection_id)
    ).first()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can't add videos to this collection")

    collection.videos_urls.extend(videos_urls)
    db.commit()
    db.refresh(collection)
    return collection