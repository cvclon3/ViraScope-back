# app/api/users.py
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request, Cookie
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session
import requests # замени на асинхрон
import uuid

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (create_access_token, get_password_hash,
                                verify_password, decode_access_token)  #  Импортируем decode_access_token
from app.models.token import Token
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserUpdate

from authlib.integrations.starlette_client import OAuth
from datetime import datetime, timedelta
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter()

# Схема OAuth2 для формы входа
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")  #  tokenUrl="token"


# Функция для получения текущего пользователя (зависимость)
async def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)  #  Используем функцию
    print('FFFFFF')
    print(payload)
    if payload is None:
        raise credentials_exception

    username: str = payload.get("sub")
    if username is None:
        raise credentials_exception
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

# Эндпоинт для регистрации пользователя
@router.post("/users/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter((User.username == user.username) | (User.email == user.email)).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username or email already registered")
    hashed_password = get_password_hash(user.password)
    db_user = User(username=user.username, email=user.email, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

# Эндпоинт для получения токена (вход)
@router.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

# Эндпоинт для получения информации о текущем пользователе
@router.get("/users/me", response_model=UserRead)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user

# Пример эндпоинта для обновления данных пользователя
@router.patch("/users/me", response_model=UserRead)
async def update_user_me(user_update: UserUpdate, current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    current_user_data = current_user.dict()
    updated_user_data = user_update.dict(exclude_unset=True)

    for key, value in updated_user_data.items():
        if key == "password":
            setattr(current_user, "hashed_password", get_password_hash(value))
        else:
            setattr(current_user, key, value)

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user

oauth = OAuth()
oauth.register(
    name="auth_demo",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    authorize_params=None,
    access_token_url="https://accounts.google.com/o/oauth2/token",
    access_token_params=None,
    refresh_token_url=None,
    authorize_state=settings.secret_key,
    redirect_uri="http://localhost:8000/auth",
    jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
    client_kwargs={"scope": "openid profile email"},
)

@router.get("/login")
async def login(request: Request):
    request.session.clear()
    referer = request.headers.get("referer")
    frontend_url = settings.frontend_url
    redirect_url = settings.redirect_url
    request.session["login_redirect"] = frontend_url
    return await oauth.auth_demo.authorize_redirect(
        request, redirect_url, prompt="consent"
    )

@router.get("/auth")
async def auth(request: Request, session: Session = Depends(get_db)):
    try:
        token = await oauth.auth_demo.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=401, detail="1 Google authentication failed.")

    try:
        user_info_endpoint = "https://www.googleapis.com/oauth2/v2/userinfo"
        headers = {"Authorization": f'Bearer {token["access_token"]}'}
        google_response = requests.get(user_info_endpoint, headers=headers)
        user_info = google_response.json()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=401, detail=f"2 Google authentication failed.{e}")

    user = token.get("userinfo")

    iss = user.get("iss")
    if iss not in ["https://accounts.google.com", "accounts.google.com"]:
        raise HTTPException(status_code=401, detail="3 Google authentication failed.")

    user_id = user.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="4 Google authentication failed.")

    expires_in = token.get("expires_in")

    user_email = user.get("email")
    user_name = user_info.get("name")
    user_pic = user_info.get("picture")


    # Create JWT token
    access_token_expires = timedelta(seconds=expires_in)
    access_token = create_access_token(data={"sub": str(user_email).split('@')[0]}, expires_delta=access_token_expires)

    # TODO: Добавить данные в бдшку
    user_ = session.query(User).filter(User.email == user_email).first()
    if not user_:
        user_ = User(email=user_email, username=str(user_email).split('@')[0], hashed_password=get_password_hash(str(uuid.uuid4())))
        session.add(user_)
        session.commit()
        session.refresh(user_)

    redirect_url = request.session.pop("login_redirect", "")
    response = RedirectResponse(redirect_url)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,  # Ensure you're using HTTPS
        samesite="strict",  # Set the SameSite attribute to None
    )

    return response
