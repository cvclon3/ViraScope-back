# app/api/auth.py
import traceback
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request, Cookie
import uuid

# Добавляем импорт Credentials
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build # Добавляем build
# --- УДАЛЯЕМ импорт _helpers ---
# from google.oauth2 import _helpers

from starlette.responses import JSONResponse
from sqlmodel import select

from app.core.config import settings
from app.core.database import SessionDep
from app.core.security import get_password_hash
from app.models.user import User

from authlib.integrations.starlette_client import OAuth
# --- Убедимся, что все нужные части datetime импортированы ---
from datetime import datetime, timedelta, timezone
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt, ExpiredSignatureError

# --- Добавляем logging ---
import logging
logging.basicConfig(level=logging.INFO) # Или logging.DEBUG
logger = logging.getLogger(__name__)

router = APIRouter()

# ... (oauth registration - no changes) ...
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
    client_kwargs={
        "scope": "openid profile email https://www.googleapis.com/auth/youtube.readonly",
    },
)


# ... (create_access_token - no changes) ...
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})
    if "google_token_expires_at" in to_encode and isinstance(to_encode["google_token_expires_at"], datetime):
         to_encode["google_token_expires_at"] = to_encode["google_token_expires_at"].timestamp()
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.algorithm)
    logger.debug(f"Created JWT Payload: {to_encode}")
    return encoded_jwt


# Функция для извлечения учетных данных Google из нашего JWT
def get_google_credentials_from_token(token: str) -> Credentials:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials for Google API",
        headers={"WWW-Authenticate": "Bearer"},
    )
    youtube_permission_exception = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="YouTube API permission not granted or token expired. Please re-login.",
    )
    session_expired_exception = HTTPException(
         status_code=status.HTTP_401_UNAUTHORIZED,
         detail="Session expired. Please login again."
    )

    logger.info(f"Decoding JWT token (length: {len(token)})...")
    if not token:
         logger.error("No token provided to get_google_credentials_from_token")
         raise credentials_exception
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.algorithm]
        )
        logger.info("JWT decoded successfully.")
        google_access_token: str = payload.get("google_access_token")
        google_token_expires_at_ts: float = payload.get("google_token_expires_at")

        if google_access_token is None or google_token_expires_at_ts is None:
            logger.error("Google token info (access_token/expires_at) missing in JWT payload")
            raise youtube_permission_exception

        try:
             google_token_expires_at = datetime.fromtimestamp(google_token_expires_at_ts, tz=timezone.utc)
        except (TypeError, ValueError) as ts_err:
             logger.error(f"Invalid timestamp format in JWT for google_token_expires_at: {google_token_expires_at_ts} ({ts_err})")
             raise youtube_permission_exception

        now_utc = datetime.now(timezone.utc)
        logger.info(f"Current time (UTC): {now_utc}")
        logger.info(f"Google token expires at (UTC from JWT): {google_token_expires_at}")

        # Проверяем истечение токена Google ПЕРЕД созданием Credentials
        if now_utc >= google_token_expires_at:
            logger.warning("Google access token from JWT has expired.")
            raise youtube_permission_exception

        # Создаем объект Credentials
        credentials = Credentials(
            token=google_access_token,
            token_uri=oauth.auth_demo.access_token_url,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            scopes=oauth.auth_demo.client_kwargs.get("scope").split()
        )

        # Устанавливаем expiry ПОСЛЕ создания, убедившись, что это aware datetime
        credentials.expiry = google_token_expires_at.astimezone(timezone.utc)

        # --- ОТЛАДКА: УДАЛЕНЫ ссылки на _helpers ---
        expiry_to_check = credentials.expiry
        current_time_to_check = datetime.now(timezone.utc) # Используем стандартный метод
        logger.info(f"Credentials expiry type: {type(expiry_to_check)}, TZ: {expiry_to_check.tzinfo}, value: {expiry_to_check}")
        logger.info(f"Current time type: {type(current_time_to_check)}, TZ: {current_time_to_check.tzinfo}, value: {current_time_to_check}")

        logger.info(f"Checking credentials validity...")
        try:
             # Эта проверка теперь должна сравнивать два Aware Datetime объекта
             is_valid = credentials.valid
        except TypeError as te:
             # Эта ошибка все еще может возникнуть, если credentials.expiry или
             # внутренний вызов now() в .valid окажутся naive.
             logger.error(f"TypeError during credentials.valid check (are datetimes aware?): {te}", exc_info=True)
             raise youtube_permission_exception
        except Exception as val_err:
             logger.error(f"Unexpected error during credentials.valid check: {val_err}", exc_info=True)
             raise youtube_permission_exception

        logger.info(f"Created Credentials object. Valid: {is_valid}")

        if not is_valid:
             logger.warning("Credentials object marked as invalid by google-auth library.")
             # Доверяем библиотеке, если она считает токен невалидным
             raise youtube_permission_exception

        return credentials

    except ExpiredSignatureError:
        logger.warning("Application JWT has expired.")
        raise session_expired_exception
    except JWTError as e:
        logger.error(f"JWTError decoding token: {e}", exc_info=True)
        raise credentials_exception
    except HTTPException as he:
         raise he
    except Exception as e:
        logger.exception("Unexpected error getting Google credentials from token")
        raise credentials_exception


# ... (get_access_token_from_cookie - no changes) ...
def get_access_token_from_cookie(request: Request) -> Optional[str]:
    token = request.cookies.get("access_token")
    logger.debug(f"Access Token from cookie: {token[:10] if token else 'None'}...")
    return token

# ... (get_google_credentials_from_cookie - no changes) ...
def get_google_credentials_from_cookie(token: Optional[str] = Depends(get_access_token_from_cookie)) -> Credentials:
    logger.debug("Attempting to get Google credentials via cookie dependency...")
    if token is None:
         logger.error("Access token cookie is missing in get_google_credentials_from_cookie")
         raise HTTPException(status_code=401, detail="Not authenticated (token missing)")
    try:
        return get_google_credentials_from_token(token=token)
    except HTTPException as he:
         logger.error(f"HTTPException propagated from get_google_credentials_from_token: {he.status_code} - {he.detail}")
         raise he
    except Exception as e:
         logger.exception("Unexpected error in get_google_credentials_from_cookie wrapper")
         raise HTTPException(status_code=500, detail="Internal error processing credentials")


# ... (get_user_youtube_client - no changes) ...
def get_user_youtube_client(credentials: Credentials = Depends(get_google_credentials_from_cookie)):
     logger.debug("Attempting to build YouTube client with user credentials...")
     if not credentials or not credentials.valid:
         logger.error("Invalid or missing credentials passed to get_user_youtube_client")
         raise HTTPException(status_code=401, detail="Invalid user credentials for YouTube API.")
     try:
         youtube = build(
             "youtube",
             "v3",
             credentials=credentials,
             cache_discovery=False
         )
         logger.info("YouTube client built successfully with user credentials.")
         return youtube
     except Exception as e:
         logger.exception("Error building YouTube client")
         raise HTTPException(status_code=500, detail=f"Could not create YouTube API client: {e}")

# ... (get_user_youtube_client_via_cookie - no changes) ...
get_user_youtube_client_via_cookie = get_user_youtube_client

# ... (get_current_user - no significant changes, UUID lookup is good) ...
def get_current_user(
    request: Request,
    session: SessionDep,
    _ : Credentials = Depends(get_google_credentials_from_cookie) # Check token validity first
    ) -> User:
    logger.debug("Attempting to get current user from DB...")
    access_token = request.cookies.get("access_token")
    if not access_token:
         logger.error("Access token missing in get_current_user (should have failed earlier)")
         raise HTTPException(status_code=401, detail="Not authenticated (token missing)")

    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials (user lookup)",
        headers={"WWW-Authenticate": "Bearer"},
    )
    session_expired_exception = HTTPException(
         status_code=status.HTTP_401_UNAUTHORIZED,
         detail="Session expired. Please login again."
    )

    try:
        # Decode *with* verification here is safer, as the dependency only checks expiry
        payload = jwt.decode(access_token, settings.jwt_secret_key, algorithms=[settings.algorithm])

        user_id_from_jwt: str = payload.get("sub")
        user_email: str = payload.get("email")

        if user_id_from_jwt is None or user_email is None:
            logger.error("Required fields (sub, email) missing in JWT payload for user lookup")
            raise credentials_exception

        try:
            user_uuid = uuid.UUID(user_id_from_jwt)
            user = session.get(User, user_uuid)
        except (ValueError, TypeError):
             logger.warning(f"Could not parse user ID '{user_id_from_jwt}' as UUID. Falling back to email lookup.")
             user = session.exec(select(User).where(User.email == user_email)).first()

        if user is None:
            logger.error(f"User with ID '{user_id_from_jwt}' or email '{user_email}' not found in DB")
            raise credentials_exception

        logger.info(f"Authenticated user from DB: {user.email} (ID: {user.id})")
        return user

    except ExpiredSignatureError:
         logger.error("ExpiredSignatureError caught in get_current_user (should have been caught earlier)")
         raise session_expired_exception
    except JWTError as e:
        logger.error(f"JWTError caught in get_current_user: {e}", exc_info=True)
        raise credentials_exception
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception("Unexpected error in get_current_user")
        raise credentials_exception


# ... (login - no changes) ...
@router.get("/login")
async def login(request: Request):
    request.session.clear()
    frontend_url = settings.frontend_url
    redirect_uri_for_google = settings.redirect_url
    request.session["login_redirect_url"] = frontend_url
    logger.info(f"Initiating Google login. Redirect URI for Google: {redirect_uri_for_google}")
    return await oauth.auth_demo.authorize_redirect(request, redirect_uri_for_google)

# ... (auth - no changes) ...
@router.get("/auth")
async def auth(request: Request, session: SessionDep):
    final_redirect_url = request.session.get("login_redirect_url", settings.frontend_url)
    try:
        logger.info("Handling /auth callback from Google...")
        token_data = await oauth.auth_demo.authorize_access_token(request)
        logger.debug(f"Received token data from Google: {token_data}")
    except Exception as e:
        logger.error(f"Error authorizing access token from Google: {e}", exc_info=True)
        error_redirect_url = final_redirect_url + "?error=google_auth_failed"
        return RedirectResponse(error_redirect_url)

    user_info = token_data.get("userinfo")
    if not user_info:
        logger.warning("Userinfo not in token response, fetching separately...")
        try:
             user_info_endpoint = "https://www.googleapis.com/oauth2/v3/userinfo"
             headers = {"Authorization": f'Bearer {token_data["access_token"]}'}
             async with httpx.AsyncClient() as client:
                 google_response = await client.get(user_info_endpoint, headers=headers)
                 google_response.raise_for_status()
                 user_info = google_response.json()
             logger.info(f"Fetched userinfo: {user_info}")
        except Exception as e:
             logger.error(f"Error fetching userinfo from Google: {e}", exc_info=True)
             error_redirect_url = final_redirect_url + "?error=google_userinfo_failed"
             return RedirectResponse(error_redirect_url)

    iss = user_info.get("iss")
    if iss not in ["https://accounts.google.com", "accounts.google.com"]:
        logger.error(f"Invalid issuer: {iss}")
        raise HTTPException(status_code=401, detail="Invalid issuer.")

    google_user_id = user_info.get("sub")
    user_email = user_info.get("email")
    if not google_user_id or not user_email:
         logger.error("User sub or email missing in userinfo")
         raise HTTPException(status_code=401, detail="User ID or email missing.")

    google_access_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in")

    if not google_access_token or not expires_in:
        logger.error("Google access token or expires_in missing in token response")
        raise HTTPException(status_code=401, detail="Google token data missing.")

    google_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    logger.info(f"Calculated Google token expiry (UTC): {google_token_expires_at}")

    user_in_db = session.exec(select(User).where(User.email == user_email)).first()
    if not user_in_db:
        logger.info(f"Creating new user for email: {user_email}")
        user_in_db = User(
            email=user_email,
            username=user_info.get("name", str(user_email).split('@')[0]),
            hashed_password=get_password_hash(str(uuid.uuid4())),
            is_active=True
        )
        session.add(user_in_db)
        session.commit()
        session.refresh(user_in_db)
        logger.info(f"Created user with DB ID: {user_in_db.id}")
    else:
        logger.info(f"Found existing user: {user_in_db.email} (DB ID: {user_in_db.id})")

    app_token_lifetime_seconds = min(
        expires_in,
        settings.access_token_expire_minutes * 60
    )
    app_token_expires_delta = timedelta(seconds=app_token_lifetime_seconds)

    jwt_data = {
        "sub": str(user_in_db.id),
        "email": user_email,
        "google_user_id": google_user_id,
        "google_access_token": google_access_token,
        "google_token_expires_at": google_token_expires_at,
    }
    app_access_token = create_access_token(data=jwt_data, expires_delta=app_token_expires_delta)

    redirect_target_url = request.session.pop("login_redirect_url", settings.frontend_url)
    logger.info(f"Authentication successful. Redirecting to: {redirect_target_url}")

    response = RedirectResponse(redirect_target_url)
    response.set_cookie(
        key="access_token",
        value=app_access_token,
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=int(app_token_expires_delta.total_seconds()),
        path="/"
    )
    return response


# ... (auth_verify - no changes) ...
@router.get("/auth/verify")
async def auth_verify(
    credentials: Optional[Credentials] = Depends(get_google_credentials_from_cookie),
    current_user: User = Depends(get_current_user)
    ):
    if credentials and credentials.valid and current_user:
         logger.info(f"Auth verify successful for user: {current_user.email}")
         return JSONResponse(status_code=200, content={"detail" : "authenticated", "user_email": current_user.email})
    else:
         logger.error("Auth verify failed unexpectedly (dependencies should have raised error).")
         raise HTTPException(status_code=401, detail="Authentication failed")

# ... (logout - no changes) ...
@router.get("/logout")
async def logout(request: Request):
    logger.info("Logout requested.")
    access_token_cookie = request.cookies.get("access_token")
    google_token_to_revoke = None

    if access_token_cookie:
        try:
            payload = jwt.decode(access_token_cookie, settings.jwt_secret_key, algorithms=[settings.algorithm], options={"verify_signature": False, "verify_exp": False})
            google_token_to_revoke = payload.get("google_access_token")
        except Exception as e:
            logger.warning(f"Could not decode access token during logout to revoke Google token: {e}")

    if google_token_to_revoke:
        try:
            async with httpx.AsyncClient() as client:
                 revoke_url = "https://oauth2.googleapis.com/revoke"
                 response = await client.post(revoke_url, params={'token': google_token_to_revoke})
                 if response.status_code == 200:
                     logger.info("Google token revoked successfully.")
                 else:
                     logger.warning(f"Failed to revoke Google token: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error during Google token revocation: {e}", exc_info=True)

    request.session.clear()
    response = JSONResponse(content={"message": "Logged out successfully."})
    response.delete_cookie("access_token", path="/")
    logger.info("User logged out, cookie deleted.")
    return response