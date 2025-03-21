# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import videos, users, favorites, search  # Импортируем favorites
from app.core.config import settings
from app.core.database import init_db
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI(title=settings.app_name)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,  # Replace with a secure secret key
    session_cookie="session_cookie_name"  # Optional: Customize the cookie name
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],  # Allow your frontend origin
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)

# Подключаем роутеры
app.include_router(videos.router, prefix="/videos", tags=["videos"])
app.include_router(users.router, prefix="", tags=["users"])
app.include_router(favorites.router, prefix="/favorites", tags=["favorites"])
app.include_router(search.router, prefix="/search", tags=["search"])


# Создаем таблицы при старте приложения
@app.on_event("startup")
async def on_startup():
    init_db()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, log_level='trace')
