# app/main.py
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html, get_redoc_html

from app.api import videos, users, favorites, search, getcomments  # Импортируем favorites
from app.core.config import settings
from app.core.database import init_db

app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None)

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
async def swagger_ui_redirect():
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/redoc", include_in_schema=False)
async def redoc_html():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=app.title + " - ReDoc",
        redoc_js_url="https://unpkg.com/redoc@next/bundles/redoc.standalone.js",
    )

# Подключаем роутеры
# app.include_router(videos.router, prefix="/videos", tags=["videos"])
app.include_router(users.router, prefix="", tags=["users"])
app.include_router(favorites.router, prefix="/favorites", tags=["favorites"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(getcomments.router, prefix="/forai", tags=["for ai"])


# Создаем таблицы при старте приложения
@app.on_event("startup")
async def on_startup():
    init_db()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, log_level='trace')
