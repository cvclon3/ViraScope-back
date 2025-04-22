# app/core/rate_limiter.py
import logging
from fastapi import Depends, HTTPException, status
import redis.asyncio as redis # Используем async клиент
import time

from app.core.config import settings
from app.core.redis_client import get_redis_client # Наша зависимость Redis
from app.models.user import User # Нужен для user.id и user.is_superuser
from app.api.auth import get_current_user # Зависимость текущего пользователя

logger = logging.getLogger(__name__)

# Ключ для Redis, можно вынести в константы или конфиг
RATE_LIMIT_SEARCH_KEY_PREFIX = "rate_limit:user"
RATE_LIMIT_ACTION = "search"

async def rate_limit_search(
    user: User = Depends(get_current_user), # Получаем объект User
    redis_client: redis.Redis = Depends(get_redis_client) # Внедряем Redis клиент
):
    """
    FastAPI зависимость для ограничения частоты запросов поиска (/search/*).
    Пропускает проверку для пользователей с флагом is_superuser.
    Использует Redis для отслеживания количества запросов обычных пользователей.
    Применяет "fail open" стратегию при ошибках Redis.
    """
    # --- >>> НОВОЕ ИЗМЕНЕНИЕ: Проверка на суперпользователя <<< ---
    if user.is_superuser:
        logger.debug(f"Rate limit check bypassed for superuser: {user.email} (ID: {user.id})")
        return True # Администраторы не ограничены, пропускаем проверку
    # --- >>> КОНЕЦ НОВОГО ИЗМЕНЕНИЯ <<< ---

    # --- Логика для обычных пользователей (без изменений) ---
    limit = settings.search_rate_limit_count
    window = settings.search_rate_limit_window_seconds
    key = f"{RATE_LIMIT_SEARCH_KEY_PREFIX}:{user.id}:{RATE_LIMIT_ACTION}"

    try:
        # 1. Атомарно увеличиваем счетчик
        current_count = await redis_client.incr(key)

        # 2. Если это ПЕРВЫЙ запрос в этом окне (счетчик стал равен 1),
        #    устанавливаем время жизни ключа.
        if current_count == 1:
            await redis_client.expire(key, window)
            logger.info(f"First search request for user {user.id} in window. Setting TTL {window}s for key {key}.")

        # 3. Проверяем лимит
        if current_count > limit:
            # Получаем актуальный TTL для заголовка Retry-After
            final_ttl = await redis_client.ttl(key)
            # Если TTL вдруг -1 (не должно быть) или -2 (уже истек?), используем полное окно
            retry_after = final_ttl if final_ttl > 0 else window
            logger.warning(f"Rate limit exceeded for user {user.id} (search). Count: {current_count}/{limit}. Retry after: {retry_after}s.")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Search limit exceeded ({limit} requests per {window // 3600} hours). Please try again later.",
                headers={"Retry-After": str(retry_after)}
            )

        logger.debug(f"Rate limit check passed for user {user.id} (search). Count: {current_count}")
        return True # Возвращаемое значение не используется, но показывает успешность

    except redis.RedisError as e:
        # Ошибка при работе с Redis - применяем "Fail open"
        logger.error(f"Redis error during rate limiting check for user {user.id}: {e}. Allowing request (Fail open).", exc_info=True)
        return True # Пропускаем проверку
    except HTTPException as e:
        # Если была выброшена HTTPException (429), пробрасываем ее дальше
        raise e
    except Exception as e:
        # Другие неожиданные ошибки - тоже "Fail open"
        logger.exception(f"Unexpected error during rate limiting check for user {user.id}: {e}. Allowing request (Fail open).")
        return True # Пропускаем проверку