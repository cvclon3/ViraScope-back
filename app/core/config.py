# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
import os


load_dotenv()


class Settings(BaseSettings):
    # youtube_api_key: str = os.getenv("YOUTUBE_API_KEY")
    app_name: str = "My YouTube App"
    flow_port: int = os.getenv("FLOW_PORT")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test.db")
    secret_key: str = os.getenv("SECRET_KEY")
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY")  # !!! Смените в .env
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))  # добавляем
    algorithm: str = os.getenv("ALGORITHM")
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_url: str = os.getenv("REDIRECT_URL")
    frontend_url: str = os.getenv("FRONTEND_URL")

    model_config = SettingsConfigDict(env_file=".env", extra="allow")


settings = Settings()
