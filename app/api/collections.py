# app/api/collections.py
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.database import get_db
from app.models.user import User
from app.models.collection import Collection
from app.schemas.collection import CollectionList, CollectionRead, CollectionCreate
from app.api.auth import get_current_user  # Используем нашу зависимость


router = APIRouter()

@router.post("/", response_model=CollectionRead, status_code=201)
async def create_collection(
    collection_title: str,
    videos_urls: List[str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создаёт новую коллекцию."""
    collection = db.exec(
        select(Collection)
        .where(Collection.user_id == current_user.id)
        .where(Collection.collection_title == collection_title)
    ).first()

    if collection:
        raise HTTPException(status_code=400, detail="Collection with this title already exists")

    collection = Collection(user_id=current_user.id, collection_title=collection_title)
    collection.videos_urls = json.dumps(videos_urls)
    db.add(collection)
    db.commit()
    db.refresh(collection)
    return CollectionRead.from_db(collection)


@router.get("/", response_model=CollectionList)
async def get_collections(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Возвращает список коллекций пользователя."""
    db_collections = db.exec(select(Collection).where(Collection.user_id == current_user.id)).all()
    collections = [
        CollectionRead.from_db(collection)
        for collection in db_collections
    ]

    return CollectionList(collections=collections)

@router.get("/{collection_id}", response_model=CollectionRead)
async def get_collection(current_user: User = Depends(get_current_user), db: Session = Depends(get_db), collection_id: int = None):
    """Возвращает коллекцию по её id"""
    if not collection_id:
        raise HTTPException(status_code=404, detail="Collection id not specified")

    collection = db.exec(
        select(Collection)
        .where(Collection.user_id == current_user.id)
        .where(Collection.id == collection_id)
    ).first()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can't add videos to this collection")

    return CollectionRead.from_db(collection)

@router.delete("/{collection_id}", status_code=204)
async def delete_collection(
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

    if collection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can't add videos to this collection")


    db.delete(collection)
    db.commit()
    return {"message": "Collection deleted"}

@router.put("/edit/{collection_id}", response_model=CollectionRead, status_code=201)
async def edit_collection(
    add_videos_urls: List[str],  # Принимаем список URL для добавления
    remove_videos_urls: List[str],  # Принимаем список URL для удаления
    collection_id: int,
    collection_title: str | None = None,
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

    if collection_title:
        collection.collection_title = collection_title

    current_urls = json.loads(collection.videos_urls)
    # Добавляем новые URL
    current_urls.extend(add_videos_urls)
    # Удаляем URL
    current_urls = [x for x in current_urls if x not in remove_videos_urls]
    # Сохраняем обновленный список как JSON
    collection.videos_urls = json.dumps(current_urls)

    db.add(collection)
    db.commit()
    db.refresh(collection)
    return CollectionRead.from_db(collection)
