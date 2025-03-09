from fastapi import FastAPI
from app.api import videos  # Импортируем роутер
from app.core.config import settings

app = FastAPI(title=settings.app_name)

app.include_router(videos.router, prefix="/videos", tags=["videos"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True) #reload для разработки