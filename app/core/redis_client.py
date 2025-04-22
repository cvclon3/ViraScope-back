# app/core/redis_client.py
import redis.asyncio as redis # Используем async версию клиента
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import HTTPException, status # Импортируем HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

# Глобальный пул соединений (рекомендуется для асинхронных приложений)
redis_pool = None

def create_redis_pool():
    """Создает пул соединений Redis. Вызывается при старте."""
    global redis_pool
    if redis_pool is None:
        if not settings.redis_url:
             logger.warning("REDIS_URL is not set. Redis functionality will be disabled.")
             return None
        try:
            logger.info(f"Creating Redis connection pool for URL: {settings.redis_url}")
            redis_pool = redis.ConnectionPool.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                health_check_interval=30
            )
            logger.info("Redis connection pool creation initiated.")
        except Exception as e:
            logger.error(f"Failed to create Redis connection pool: {e}", exc_info=True)
            redis_pool = None
    return redis_pool

async def close_redis_pool():
    """Закрывает пул соединений Redis. Вызывается при остановке."""
    global redis_pool
    if redis_pool:
        logger.info("Closing Redis connection pool.")
        try:
            await redis_pool.disconnect(inuse_connections=True)
            logger.info("Redis connection pool closed.")
        except Exception as e:
             logger.error(f"Error closing Redis connection pool: {e}", exc_info=True)
        finally:
             redis_pool = None

async def get_redis_client() -> AsyncGenerator[redis.Redis, None]:
    """
    FastAPI зависимость для получения асинхронного клиента Redis из пула.
    Обрабатывает ошибки подключения к пулу.
    Не перехватывает HTTPException от зависимых функций.
    """
    global redis_pool
    if redis_pool is None:
        logger.warning("Redis pool was None, attempting to create it now.")
        create_redis_pool()
        if redis_pool is None:
            logger.error("Redis pool is not available. Cannot get Redis client.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Internal error: Cache service connection failed."
            )

    client = None
    try:
        client = redis.Redis(connection_pool=redis_pool)
        await client.ping() # Проверяем соединение
        yield client # Возвращаем клиент зависимой функции (rate_limiter)
    except redis.ConnectionError as e:
         logger.error(f"Redis connection error: {e}", exc_info=True)
         raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Internal error: Cache service unavailable."
            )
    # --- ИЗМЕНЕННЫЙ БЛОК except ---
    except Exception as e:
        # Ловим *только* ошибки, возникшие ВНУТРИ этого try блока
        # (например, ошибка ping() не являющаяся ConnectionError)
        # ВАЖНО: Не ловим здесь HTTPException, чтобы ошибки 429 и т.д.
        # от зависимостей (rate_limiter) пробрасывались выше без изменений.
        if isinstance(e, HTTPException):
             # Если каким-то образом сюда попала HTTPException (не должна),
             # пробрасываем ее без изменений.
             raise e
        # Логируем и преобразуем другие неожиданные ошибки в 500
        logger.error(f"Failed to get or use Redis client from pool: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error processing cache request."
        )
    # --- КОНЕЦ ИЗМЕНЕННОГО БЛОКА ---
    finally:
        # Клиент из пула не требует закрытия здесь.
        pass


# Lifespan manager для FastAPI (без изменений)
@asynccontextmanager
async def lifespan(app):
    print("Application startup: Initializing database and Redis pool...")
    from app.core.database import init_db
    init_db()
    create_redis_pool()
    print("Initialization complete.")
    yield
    print("Application shutdown: Closing Redis pool...")
    await close_redis_pool()
    print("Redis pool closed.")