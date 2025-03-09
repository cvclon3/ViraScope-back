# services/user.py
import os
import uuid
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (AuthenticationBackend,
                                            BearerTransport, JWTStrategy)
from fastapi_users.db import SQLAlchemyUserDatabase #Используем
from httpx_oauth.clients.google import GoogleOAuth2

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserUpdate
# from sqlalchemy.ext.asyncio import AsyncSession #Удалить если не используется async

SECRET = settings.secret_key
bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")

def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)
class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        print(f"User {user.id} has registered.")

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"Verification requested for user {user.id}. Verification token: {token}")

async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(SQLAlchemyUserDatabase(UserRead, UserCreate,get_db, User))):
    yield UserManager(user_db)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend], UserRead, UserCreate, UserUpdate, User)
current_active_user = fastapi_users.current_user(active=True)

if settings.google_oauth_client_id and settings.google_oauth_client_secret:
    google_oauth_client = GoogleOAuth2(settings.google_oauth_client_id, settings.google_oauth_client_secret)
else:
    google_oauth_client = None