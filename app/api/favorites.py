# app/api/favorites.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List
import re
import datetime
import traceback # Для отладки

from app.core.database import get_db, SessionDep # Используем SessionDep
from app.models.user import User
from app.models.favorite import FavoriteChannel
from app.schemas.favorite import FavoriteChannelCreate, FavoriteChannelRead, FavoriteChannelList
from app.api.auth import get_current_user, get_user_youtube_client_via_cookie # Импортируем обе зависимости
# Импортируем функции ядра для вызова с клиентом
from app.core.youtube import get_channel_info as core_get_channel_info
from app.core.youtube import get_total_videos_on_channel as core_get_total_videos
# Импортируем тип клиента YouTube
from googleapiclient.discovery import build

router = APIRouter()

def extract_channel_id(url: str) -> str | None:
    """Извлекает ID канала из URL."""
    # Паттерны для разных форматов URL каналов
    patterns = [
        r"youtube\.com/(?:channel/|@)([\w-]+)", # /channel/UC..., /@username
        r"youtube\.com/user/([\w-]+)",         # /user/username (старый формат)
        r"youtube\.com/c/([\w-]+)",           # /c/customName (старый формат)
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            potential_id_or_name = match.group(1)
            # Проверяем, похож ли он на стандартный ID (UC...)
            if potential_id_or_name.startswith("UC") and len(potential_id_or_name) == 24:
                 return potential_id_or_name
            # Если не похож на ID, это может быть имя пользователя или кастомное имя
            # В этом случае потребуется дополнительный запрос к API для поиска ID по имени
            # Пока возвращаем как есть, но с пометкой, что это может быть не ID
            print(f"Extracted potential non-ID identifier: {potential_id_or_name} from URL: {url}")
            # Для простоты пока считаем, что извлекаем только ID формата UC...
            # Если нужно обрабатывать имена, потребуется функция resolve_channel_name_to_id(youtube, name)
            # return potential_id_or_name # Возвращаем как есть, если нужна обработка имен
            # Вернем None, если не стандартный ID, для текущей логики
            if not (potential_id_or_name.startswith("UC") and len(potential_id_or_name) == 24):
                print(f"Identifier {potential_id_or_name} is not a standard channel ID (UC...). Skipping URL: {url}")
                return None # Пропускаем URL, если не стандартный ID
            return potential_id_or_name # Возвращаем только стандартные ID
    return None

# --- ИЗМЕНЕНИЕ: Добавляем зависимость youtube клиента ---
@router.post("/", response_model=List[FavoriteChannelRead], status_code=201) # Убрали /favorites из пути
async def add_favorite_channels(
    channel_urls: List[str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db), # Используем get_db напрямую
    # --- Новая зависимость ---
    youtube: build = Depends(get_user_youtube_client_via_cookie)
):
    """Добавляет каналы в избранное пользователя."""
    added_channels_db = []
    errors = []

    for url in channel_urls:
        channel_id = extract_channel_id(url)
        if not channel_id:
            errors.append({"url": url, "error": "Invalid or unsupported channel URL format. Only URLs with /channel/UC... IDs are currently supported."})
            print(f"Skipping invalid URL: {url}")
            continue

        print(f"Processing channel ID: {channel_id} for user {current_user.email}")

        # Проверяем, есть ли уже такой канал у этого пользователя
        existing_channel = db.exec(
            select(FavoriteChannel)
            .where(FavoriteChannel.user_id == current_user.id)
            .where(FavoriteChannel.channel_id == channel_id)
        ).first()
        if existing_channel:
            print(f"Channel {channel_id} already in favorites for user {current_user.email}. Skipping.")
            # Можно добавить его в ответ, если нужно вернуть все запрошенные (даже существующие)
            # added_channels_db.append(existing_channel)
            continue

        # --- ИЗМЕНЕНИЕ: Получаем информацию о канале с YouTube используя youtube клиент ---
        try:
            print(f"Fetching channel info from YouTube API for: {channel_id}")
            channel_info_dict = await core_get_channel_info(youtube, channel_id)
            if not channel_info_dict:
              # Эта ошибка обрабатывается внутри core_get_channel_info, но проверим еще раз
              print(f"Failed to get channel info for {channel_id} from API.")
              errors.append({"url": url, "channel_id": channel_id, "error": "Channel not found or API error."})
              continue # Переходим к следующему URL

            # --- ИЗМЕНЕНИЕ: Получаем количество видео с youtube клиентом ---
            video_count = core_get_total_videos(youtube, channel_id)
            # Обработка случая, когда video_count равен None (ошибка API)
            if video_count is None:
                 print(f"Failed to get video count for {channel_id}. Setting to 0.")
                 video_count = 0 # Устанавливаем значение по умолчанию или пропускаем канал

            # Ищем последнее видео на канале, чтобы узнать дату публикации
            print(f"Searching for the last video on channel: {channel_id}")
            search_response = youtube.search().list(
                part='snippet',
                channelId=channel_id,
                type='video',
                order='date', # Сортировка по дате (сначала новые)
                maxResults=1
            ).execute()

            last_published_at = None
            if search_response.get('items'):
                published_str = search_response['items'][0]['snippet'].get('publishedAt')
                if published_str:
                     try:
                          # Преобразуем строку в datetime с таймзоной UTC
                          last_published_at = datetime.datetime.fromisoformat(published_str.replace('Z', '+00:00'))
                     except ValueError:
                          print(f"Could not parse last published date: {published_str}")
                          last_published_at = datetime.datetime.now(datetime.timezone.utc) # Fallback

            else:
                print(f"No videos found on channel {channel_id} to determine last published date.")
                # Устанавливаем текущую дату или None, в зависимости от требований
                last_published_at = datetime.datetime.now(datetime.timezone.utc) # Или None

            # Создаем объект FavoriteChannel для БД
            favorite_channel = FavoriteChannel(
                user_id=current_user.id,
                channel_id=channel_id,
                channel_title=channel_info_dict.get('channel_title', 'Unknown Title'),
                channel_thumbnail=channel_info_dict.get('channel_thumbnail', ''),
                channel_subscribers=channel_info_dict.get('channel_subscribers', 0),
                channel_video_count=video_count, # Используем полученное количество видео
                channel_last_published_at=last_published_at, # Используем полученную дату
                channel_url=channel_info_dict.get('channel_url', f'https://www.youtube.com/channel/{channel_id}'),
                # added_at устанавливается по умолчанию в модели
            )
            db.add(favorite_channel)
            added_channels_db.append(favorite_channel)
            print(f"Prepared channel {channel_id} for adding to DB.")

        except HTTPException as he:
             # Перехватываем HTTP ошибки от зависимостей (например, 401 от get_user_youtube_client)
             print(f"HTTPException while processing channel {channel_id}: {he.detail}")
             errors.append({"url": url, "channel_id": channel_id, "error": f"API Auth/Permission Error: {he.detail}"})
             # Возможно, стоит прервать весь процесс при ошибке авторизации
             # raise he
        except Exception as e:
            print(f"Unexpected error processing channel {channel_id}: {e}")
            traceback.print_exc()
            errors.append({"url": url, "channel_id": channel_id, "error": f"Internal error: {e}"})
            # Можно откатить транзакцию, если нужно атомарное добавление
            # db.rollback()
            # raise HTTPException(status_code=500, detail=f"Error processing channel {channel_id}: {e}")

    # Коммитим все успешно добавленные каналы
    if added_channels_db:
        try:
            db.commit()
            print(f"Committed {len(added_channels_db)} new favorite channels to DB.")
            # Обновляем объекты из БД, чтобы получить ID и added_at
            for ch in added_channels_db:
                db.refresh(ch)
        except Exception as e:
             print(f"Error committing favorites to DB: {e}")
             db.rollback()
             raise HTTPException(status_code=500, detail=f"Database commit error: {e}")

    # Возвращаем список успешно добавленных каналов
    # Если были ошибки, можно вернуть их в заголовке или в теле ответа (если изменить response_model)
    if errors:
         print(f"Errors occurred during add_favorite_channels: {errors}")
         # Можно выбросить исключение, если хотя бы одна ошибка критична
         # raise HTTPException(status_code=400, detail={"added": [FavoriteChannelRead.model_validate(ch) for ch in added_channels_db], "errors": errors})

    # Валидируем через Pydantic модель перед возвратом
    return [FavoriteChannelRead.model_validate(channel) for channel in added_channels_db]

# --- Эндпоинты get и delete не требуют клиента YouTube ---
@router.get("/", response_model=FavoriteChannelList) # Убрали /favorites из пути
async def get_favorite_channels(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db) # Используем get_db
):
    """Возвращает список избранных каналов пользователя из базы данных."""
    print(f"Fetching favorite channels for user: {current_user.email}")
    channels = db.exec(
        select(FavoriteChannel).where(FavoriteChannel.user_id == current_user.id)
    ).all()
    print(f"Found {len(channels)} favorite channels in DB.")
    # Модель FavoriteChannelList ожидает словарь {"channels": [...]}
    return FavoriteChannelList(channels=[FavoriteChannelRead.model_validate(ch) for ch in channels])


@router.delete("/{channel_id_db}", status_code=status.HTTP_204_NO_CONTENT) # Используем другое имя параметра пути
async def delete_favorite_channel(
    channel_id_db: str, # ID канала из пути
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db) # Используем get_db
):
    """Удаляет канал из избранного пользователя по ID канала."""
    print(f"Attempting to delete favorite channel {channel_id_db} for user {current_user.email}")
    channel = db.exec(
        select(FavoriteChannel)
        .where(FavoriteChannel.user_id == current_user.id)
        .where(FavoriteChannel.channel_id == channel_id_db) # Сравниваем с channel_id
    ).first()

    if not channel:
        print(f"Channel {channel_id_db} not found in favorites for user {current_user.email}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found in favorites")

    try:
        db.delete(channel)
        db.commit()
        print(f"Successfully deleted channel {channel_id_db} from favorites for user {current_user.email}")
        # Возвращаем пустой ответ со статусом 204
        # return {"message": "Channel deleted from favorites"} # Не нужно для 204
    except Exception as e:
        print(f"Error deleting favorite channel {channel_id_db} from DB: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database delete error: {e}")