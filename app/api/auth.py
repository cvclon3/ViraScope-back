# app/api/auth.py
import traceback
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request, Cookie
from sqlmodel import Session
import requests # замени на асинхрон
import uuid

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_password_hash
from app.models.user import User

from authlib.integrations.starlette_client import OAuth
from datetime import datetime, timedelta
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt, ExpiredSignatureError

router = APIRouter()

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
    redirect_uri=settings.redirect_url,
    jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
    client_kwargs={"scope": "openid profile email"},
)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.algorithm)


def get_current_user(token: str = Cookie(None), session: Session = Depends(get_db)):
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.algorithm])

        user_id: str = payload.get("sub")
        user_email: str = payload.get("email")

        if user_id is None or user_email is None:
            raise credentials_exception

        # Мб по id?
        user = session.query(User).filter(User.email == user_email).first()
        if user is None:
            raise credentials_exception
        return user

    except ExpiredSignatureError:
        # Specifically handle expired tokens
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please login again.")
    except JWTError:
        # Handle other JWT-related errors
        traceback.print_exc()
        raise credentials_exception
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=401, detail="Not Authenticated")

def validate_user_request(token: str = Cookie(None)):
    session_details = get_current_user(token)

    return session_details


@router.get("/login")
async def login(request: Request):
    request.session.clear()
    referer = request.headers.get("referer")
    frontend_url = settings.frontend_url
    redirect_url = settings.redirect_url
    request.session["login_redirect"] = frontend_url

    return await oauth.auth_demo.authorize_redirect(request, redirect_url, prompt="consent")


@router.get("/auth")
async def auth(request: Request, session: Session = Depends(get_db)):
    # try:
    token = await oauth.auth_demo.authorize_access_token(request)
    # except Exception as e:
    #     raise HTTPException(status_code=401, detail="Google authentication failed.")

    # try:
    user_info_endpoint = "https://www.googleapis.com/oauth2/v2/userinfo"
    headers = {"Authorization": f'Bearer {token["access_token"]}'}
    google_response = requests.get(user_info_endpoint, headers=headers)
    user_info = google_response.json()
    # except Exception as e:
    #     raise HTTPException(status_code=401, detail="Google authentication failed.")

    user = token.get("userinfo")

    iss = user.get("iss")
    if iss not in ["https://accounts.google.com", "accounts.google.com"]:
        raise HTTPException(status_code=401, detail="Google authentication failed.")

    user_id = user.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Google authentication failed.")

    user_email = user.get("email")

    # Create JWT token
    expires_in = token.get("expires_in")
    access_token_expires = timedelta(seconds=expires_in)
    access_token = create_access_token(data={"sub": user_id, "email": user_email}, expires_delta=access_token_expires)

    user_ = session.query(User).filter(User.email == user_email).first()
    if not user_:
        user_ = User(email=user_email, username=str(user_email).split('@')[0],
                     hashed_password=get_password_hash(str(uuid.uuid4())))
        # Надо ли пароль?
        session.add(user_)
        session.commit()
        session.refresh(user_)

    redirect_url = request.session.pop("login_redirect", "")
    response = RedirectResponse(redirect_url)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=False,  # Ensure you're using HTTPS
        samesite="strict",  # Set the SameSite attribute to None
    )

    return response
