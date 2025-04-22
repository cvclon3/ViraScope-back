# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
import os
from typing import Optional

load_dotenv()

class Settings(BaseSettings):
    # youtube_api_key: str = os.getenv("YOUTUBE_API_KEY")
    youtube_api_keys: Optional[str] = os.getenv("YOUTUBE_API_KEYS") # Ключи через запятую

    # --- Application Settings ---
    app_name: str = "My YouTube App"
    flow_port: int = int(os.getenv("FLOW_PORT", 8080)) # Добавил default

    # --- Database ---
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db") # Используем aiosqlite для async

    # --- Security & Auth ---
    secret_key: str = os.getenv("SECRET_KEY", "default_secret_key_change_me") # Добавил default
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "default_jwt_secret_key_change_me") # Добавил default
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24 * 7)) # Увеличил default до недели
    algorithm: str = os.getenv("ALGORITHM", "HS256") # Добавил default

    # --- Google OAuth ---
    google_client_id: Optional[str] = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret: Optional[str] = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_url: Optional[str] = os.getenv("REDIRECT_URL") # OAuth Callback URL

    # --- Frontend ---
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5173") # Добавил default

    # --- Redis & Rate Limiting ---
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    search_rate_limit_count: int = int(os.getenv("SEARCH_RATE_LIMIT_COUNT", 3))
    search_rate_limit_window_seconds: int = int(os.getenv("SEARCH_RATE_LIMIT_WINDOW_SECONDS", 6 * 60 * 60)) # 6 часов

    model_config = SettingsConfigDict(env_file=".env", extra="ignore") # Используем ignore вместо allow

settings = Settings()

# Проверка наличия обязательных ключей при необходимости
# if not settings.google_client_id or not settings.google_client_secret:
#     raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")