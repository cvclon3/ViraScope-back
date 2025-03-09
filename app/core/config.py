from pydantic_settings import BaseSettings, SettingsConfigDict  # Импортируем из pydantic-settings
from dotenv import load_dotenv
import os

load_dotenv()

class Settings(BaseSettings):
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY")
    app_name: str = "My YouTube App"
    flow_port: int = os.getenv("FLOW_PORT")

    model_config = SettingsConfigDict(env_file=".env") # Вместо class Config

settings = Settings()
