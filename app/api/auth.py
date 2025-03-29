# app/api/auth.py
import traceback
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request, Cookie
import uuid

# Добавляем импорт Credentials
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build # Добавляем build

from starlette.responses import JSONResponse
from sqlmodel import select
from starlette.requests import Request as StarletteRequest # Alias for type hinting

from app.core.config import settings
from app.core.database import SessionDep, get_db # Import get_db if SessionDep isn't sufficient everywhere
from app.core.security import get_password_hash
from app.models.user import User
from app.models.collection import Collection # Import related models if needed
from app.models.favorite import FavoriteChannel # Import related models if needed

from authlib.integrations.starlette_client import OAuth
# --- Убедимся, что все нужные части datetime импортированы ---
from datetime import datetime, timedelta, timezone
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt, ExpiredSignatureError

# --- Добавляем logging ---
import logging
logging.basicConfig(level=logging.INFO) # Или logging.DEBUG for more detail
logger = logging.getLogger(__name__)

router = APIRouter()

oauth = OAuth()
oauth.register(
    name="auth_demo",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    authorize_params=None, # Add {'access_type': 'offline', 'prompt': 'consent'} for refresh token
    access_token_url="https://accounts.google.com/o/oauth2/token",
    access_token_params=None,
    refresh_token_url=None, # Set to access_token_url if using refresh tokens
    authorize_state=settings.secret_key,
    redirect_uri=settings.redirect_url, # Where Google redirects after auth
    jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
    client_kwargs={
        "scope": "openid profile email https://www.googleapis.com/auth/youtube.readonly",
        # Add other scopes if needed
        # "prompt": "consent" # Force consent screen every time if needed
    },
)

# --- Helper Functions ---

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Creates the application's internal JWT access token."""
    to_encode = data.copy()
    # Calculate expiry time in UTC
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})

    # Convert datetime objects in payload to timestamps for JWT compatibility
    if "google_token_expires_at" in to_encode and isinstance(to_encode["google_token_expires_at"], datetime):
         to_encode["google_token_expires_at"] = to_encode["google_token_expires_at"].timestamp()

    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.algorithm)
    logger.debug(f"Created JWT Payload: {to_encode}")
    return encoded_jwt

# --- Core Dependency Functions ---

def get_google_credentials_from_token(token: str) -> Credentials:
    """
    Decodes the application's JWT, extracts Google token info, performs manual
    expiry check, and creates a Credentials object (without setting expiry).
    """
    # Define specific exceptions for clarity
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials for Google API",
        headers={"WWW-Authenticate": "Bearer"},
    )
    youtube_permission_exception = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="YouTube API permission not granted or Google token expired. Please re-login.",
    )
    session_expired_exception = HTTPException(
         status_code=status.HTTP_401_UNAUTHORIZED,
         detail="Session expired. Please login again."
    )

    logger.info(f"Attempting to get Google credentials from JWT (length: {len(token)})...")
    if not token:
         logger.error("No token provided to get_google_credentials_from_token")
         raise credentials_exception # Should use credentials_exception as it's about validating our token first

    try:
        # Decode and validate OUR application's JWT
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.algorithm] # Verifies signature and expiry
        )
        logger.info("Application JWT decoded and validated successfully.")

        # Extract Google token details from the payload
        google_access_token: str = payload.get("google_access_token")
        google_token_expires_at_ts: float = payload.get("google_token_expires_at")

        if google_access_token is None or google_token_expires_at_ts is None:
            logger.error("Google token info (access_token/expires_at) missing in JWT payload")
            # This implies an issue during JWT creation or a compromised token
            raise youtube_permission_exception # Treat as permission issue

        # Convert timestamp back to aware datetime object
        try:
             google_token_expires_at = datetime.fromtimestamp(google_token_expires_at_ts, tz=timezone.utc)
        except (TypeError, ValueError) as ts_err:
             logger.error(f"Invalid timestamp format in JWT for google_token_expires_at: {google_token_expires_at_ts} ({ts_err})")
             raise youtube_permission_exception # Invalid data

        now_utc = datetime.now(timezone.utc)
        logger.info(f"Current time (UTC): {now_utc}")
        logger.info(f"Google token expires at (UTC from JWT): {google_token_expires_at}")

        # --- MANUAL EXPIRY CHECK (Aware vs Aware) ---
        # This is our primary defense against using expired Google tokens
        if now_utc >= google_token_expires_at:
            logger.warning("Google access token from JWT has expired (manual check).")
            raise youtube_permission_exception # Raise permission error as Google token is expired

        # Create Credentials object with only the token if not using refresh tokens
        # If refresh tokens are implemented, add refresh_token, token_uri, client_id, client_secret, scopes
        credentials = Credentials(
            token=google_access_token,
            # refresh_token=payload.get("google_refresh_token"),
            # token_uri=oauth.auth_demo.access_token_url,
            # client_id=settings.google_client_id,
            # client_secret=settings.google_client_secret,
            # scopes=oauth.auth_demo.client_kwargs.get("scope").split()
        )

        # --- DO NOT SET credentials.expiry ---
        # We rely on the manual check above and avoid potential TypeErrors in the library
        logger.info("Created Credentials object WITHOUT setting expiry attribute (manual check passed).")

        # Return the basic Credentials object
        return credentials

    except ExpiredSignatureError:
        # Our application JWT itself has expired
        logger.warning("Application JWT has expired.")
        raise session_expired_exception
    except JWTError as e:
        # Other JWT validation errors (e.g., bad signature)
        logger.error(f"JWTError decoding/validating application token: {e}", exc_info=True)
        raise credentials_exception # Generic validation error for our token
    except HTTPException as he:
        # Re-raise specific HTTP exceptions if they were raised above
         raise he
    except Exception as e:
        # Catch any other unexpected errors
        logger.exception("Unexpected error getting Google credentials from token")
        raise credentials_exception # Generic validation error


def get_access_token_from_cookie(request: Request) -> Optional[str]:
    """Dependency to extract the access_token cookie."""
    token = request.cookies.get("access_token")
    logger.debug(f"Access Token from cookie: {token[:10] if token else 'None'}...")
    return token


def get_google_credentials_from_cookie(token: Optional[str] = Depends(get_access_token_from_cookie)) -> Credentials:
    """Dependency to get Google Credentials using the token from the cookie."""
    logger.debug("Attempting to get Google credentials via cookie dependency...")
    if token is None:
         logger.error("Access token cookie is missing in get_google_credentials_from_cookie")
         # Raise 401 as the user is not authenticated with our app
         raise HTTPException(status_code=401, detail="Not authenticated (token missing)")
    try:
        # Call the core function to process the token
        return get_google_credentials_from_token(token=token)
    except HTTPException as he:
         # Propagate exceptions raised by get_google_credentials_from_token
         logger.error(f"HTTPException propagated from get_google_credentials_from_token: {he.status_code} - {he.detail}")
         raise he
    except Exception as e:
         # Should not happen if inner function catches exceptions properly
         logger.exception("Unexpected error in get_google_credentials_from_cookie wrapper")
         raise HTTPException(status_code=500, detail="Internal error processing credentials")


def get_user_youtube_client(credentials: Credentials = Depends(get_google_credentials_from_cookie)) -> build:
    """Dependency to build the YouTube API client using user credentials."""
    logger.debug("Attempting to build YouTube client with user credentials...")
    # Basic check if credentials object exists (it should if previous dependency passed)
    if not credentials:
        logger.error("Missing credentials object passed to get_user_youtube_client (should not happen)")
        raise HTTPException(status_code=401, detail="Invalid user credentials for YouTube API.")

    # --- No check for credentials.valid here due to potential TypeError ---
    # The manual expiry check was done in get_google_credentials_from_token

    try:
        # Build the YouTube Data API client (v3)
        youtube = build(
            "youtube",
            "v3",
            credentials=credentials,
            cache_discovery=False # Disable discovery caching with dynamic credentials
        )
        logger.info("YouTube client built successfully with user credentials.")
        return youtube
    except Exception as e:
        # Catch errors during client building (e.g., network issues, library errors)
        logger.exception("Error building YouTube client")
        raise HTTPException(status_code=500, detail=f"Could not create YouTube API client: {e}")

# Alias for easier import in other modules
get_user_youtube_client_via_cookie = get_user_youtube_client


def get_current_user(
    request: Request, # Request needed to re-read cookie for payload
    session: SessionDep,
    # Implicitly checks token validity via this dependency
    _ : Credentials = Depends(get_google_credentials_from_cookie)
    ) -> User:
    """Dependency to get the current User object from the database."""
    logger.debug("Attempting to get current user from DB...")
    # Re-get token to extract user identifier (sub)
    access_token = request.cookies.get("access_token")
    if not access_token:
         # Should have been caught by get_google_credentials_from_cookie
         logger.error("Access token missing in get_current_user (should have failed earlier)")
         raise HTTPException(status_code=401, detail="Not authenticated (token missing)")

    # Define exceptions for this specific function's context
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
        # Decode again to get user ID (sub). Validation (sig, exp) already done.
        # Consider passing payload if optimizing, but decoding again is safer.
        payload = jwt.decode(access_token, settings.jwt_secret_key, algorithms=[settings.algorithm])

        user_id_from_jwt: str = payload.get("sub") # Expecting our internal User ID (UUID)
        user_email: str = payload.get("email") # For logging/fallback

        if user_id_from_jwt is None:
            logger.error("Required field 'sub' (user ID) missing in JWT payload for user lookup")
            raise credentials_exception

        # Lookup user by internal ID (sub)
        try:
            user_uuid = uuid.UUID(user_id_from_jwt) # Convert sub to UUID
            user = session.get(User, user_uuid) # Use Session.get for primary key lookup
        except (ValueError, TypeError):
             logger.error(f"Invalid user ID format in JWT 'sub': {user_id_from_jwt}. Cannot look up user.")
             raise credentials_exception # Invalid token content

        # Check if user exists in DB
        if user is None:
            logger.error(f"User with ID '{user_id_from_jwt}' not found in DB (associated with email '{user_email}').")
            # This could happen if user was deleted after token issuance.
            # Treat as invalid credentials for this session.
            raise credentials_exception

        logger.info(f"Authenticated user from DB: {user.email} (ID: {user.id})")
        return user

    except ExpiredSignatureError:
         # Should have been caught by get_google_credentials_from_token
         logger.error("ExpiredSignatureError caught in get_current_user (should have been caught earlier)")
         raise session_expired_exception
    except JWTError as e:
        # Should have been caught by get_google_credentials_from_token
        logger.error(f"JWTError caught in get_current_user: {e}", exc_info=True)
        raise credentials_exception
    except HTTPException as he:
        # Propagate specific HTTP errors
        raise he
    except Exception as e:
        # Catch-all for unexpected errors during user lookup
        logger.exception("Unexpected error in get_current_user")
        raise credentials_exception


# --- API Endpoints ---

@router.get("/login", tags=["auth"])
async def login(request: Request):
    """Initiates the Google OAuth2 login flow."""
    request.session.clear() # Clear any previous session state
    frontend_url = settings.frontend_url
    # The URI Google redirects to *after* user grants permission
    redirect_uri_for_google = settings.redirect_url
    # Store the final frontend URL where we want to land *after* our /auth handles the callback
    request.session["login_redirect_url"] = frontend_url

    logger.info(f"Initiating Google login. Redirect URI for Google: {redirect_uri_for_google}")
    logger.info(f"Final redirect target after /auth: {frontend_url}")

    # Redirect the user to Google's authorization page
    return await oauth.auth_demo.authorize_redirect(request, redirect_uri_for_google)


@router.get("/auth", tags=["auth"], include_in_schema=False) # Hide from OpenAPI docs
async def auth(request: Request, session: SessionDep):
    """Handles the callback from Google after user authorization."""
    # Get the final redirect URL stored in the session
    final_redirect_url = request.session.get("login_redirect_url", settings.frontend_url)
    try:
        logger.info("Handling /auth callback from Google...")
        # Exchange the authorization code for tokens
        token_data = await oauth.auth_demo.authorize_access_token(request)
        logger.debug(f"Received token data from Google: {token_data}") # Be careful logging tokens
    except Exception as e:
        logger.error(f"Error authorizing access token from Google: {e}", exc_info=True)
        # Redirect back to frontend with error parameter
        error_redirect_url = final_redirect_url + "?error=google_auth_failed"
        return RedirectResponse(error_redirect_url)

    # Extract user info (prefer from id_token if available, else fetch)
    user_info = token_data.get("userinfo") # Authlib usually includes parsed id_token here
    if not user_info:
        logger.warning("Userinfo not in token response, fetching separately...")
        try:
             user_info_endpoint = "https://www.googleapis.com/oauth2/v3/userinfo"
             headers = {"Authorization": f'Bearer {token_data["access_token"]}'}
             async with httpx.AsyncClient() as client:
                 google_response = await client.get(user_info_endpoint, headers=headers)
                 google_response.raise_for_status() # Raise exception for non-2xx status
                 user_info = google_response.json()
             logger.info(f"Fetched userinfo: {user_info}")
        except Exception as e:
             logger.error(f"Error fetching userinfo from Google: {e}", exc_info=True)
             error_redirect_url = final_redirect_url + "?error=google_userinfo_failed"
             return RedirectResponse(error_redirect_url)

    # Validate issuer
    iss = user_info.get("iss")
    if iss not in ["https://accounts.google.com", "accounts.google.com"]:
        logger.error(f"Invalid issuer: {iss}")
        # Return error to user instead of raising server error if possible
        error_redirect_url = final_redirect_url + "?error=invalid_issuer"
        return RedirectResponse(error_redirect_url)
        # raise HTTPException(status_code=401, detail="Invalid issuer.") # Or raise server error

    # Get essential identifiers
    google_user_id = user_info.get("sub") # Google's unique ID for the user
    user_email = user_info.get("email")
    if not google_user_id or not user_email:
         logger.error("User sub or email missing in userinfo")
         error_redirect_url = final_redirect_url + "?error=missing_user_data"
         return RedirectResponse(error_redirect_url)
         # raise HTTPException(status_code=401, detail="User ID or email missing.")

    # Get Google token details
    google_access_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in") # Lifetime in seconds

    if not google_access_token or not expires_in:
        logger.error("Google access token or expires_in missing in token response")
        error_redirect_url = final_redirect_url + "?error=missing_token_data"
        return RedirectResponse(error_redirect_url)
        # raise HTTPException(status_code=401, detail="Google token data missing.")

    # Calculate Google token expiry time (use aware UTC datetime)
    try:
        google_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        logger.info(f"Calculated Google token expiry (UTC): {google_token_expires_at}")
    except ValueError:
        logger.error(f"Invalid expires_in value received: {expires_in}")
        error_redirect_url = final_redirect_url + "?error=invalid_expiry_data"
        return RedirectResponse(error_redirect_url)


    # --- User Provisioning/Lookup in our Database ---
    user_in_db = session.exec(select(User).where(User.email == user_email)).first()
    if not user_in_db:
        logger.info(f"Creating new user for email: {user_email}")
        user_in_db = User(
            # Let UUID generate automatically if primary_key=True, default_factory=uuid.uuid4
            email=user_email,
            username=user_info.get("name", str(user_email).split('@')[0]), # Use Google name or generate
            hashed_password=get_password_hash(str(uuid.uuid4())), # Generate random secure password
            is_active=True # New users are active by default
        )
        session.add(user_in_db)
        try:
            session.commit()
            session.refresh(user_in_db)
            logger.info(f"Created user with DB ID: {user_in_db.id}")
        except Exception as db_err:
            logger.error(f"Database error creating user: {db_err}", exc_info=True)
            session.rollback()
            error_redirect_url = final_redirect_url + "?error=user_creation_failed"
            return RedirectResponse(error_redirect_url)
    else:
        logger.info(f"Found existing user: {user_in_db.email} (DB ID: {user_in_db.id})")
        # Optionally update user details (e.g., username) if they changed in Google
        # user_in_db.username = user_info.get("name", user_in_db.username)
        # session.add(user_in_db)
        # session.commit()
        # session.refresh(user_in_db)

    # --- Create OUR Application's JWT ---
    # Determine lifetime: shorter of Google token lifetime or our configured max lifetime
    app_token_lifetime_seconds = min(
        int(expires_in), # Use the actual lifetime from Google
        settings.access_token_expire_minutes * 60
    )
    app_token_expires_delta = timedelta(seconds=app_token_lifetime_seconds)

    jwt_data = {
        "sub": str(user_in_db.id), # Use OUR internal user ID as the subject
        "email": user_email, # Include email for convenience
        "google_user_id": google_user_id, # Store Google ID for reference
        "google_access_token": google_access_token, # Embed Google token
        "google_token_expires_at": google_token_expires_at, # Store aware datetime object here
        # "google_refresh_token": token_data.get("refresh_token"), # Store if using refresh tokens
    }
    app_access_token = create_access_token(data=jwt_data, expires_delta=app_token_expires_delta)

    # Redirect user back to the frontend, setting the JWT cookie
    redirect_target_url = request.session.pop("login_redirect_url", settings.frontend_url) # Get stored URL or default
    logger.info(f"Authentication successful. Redirecting user to: {redirect_target_url}")

    response = RedirectResponse(redirect_target_url)
    response.set_cookie(
        key="access_token",
        value=app_access_token,
        httponly=True, # Prevent JavaScript access
        secure=True, # Transmit only over HTTPS
        samesite="Lax", # Recommended for most cases ('Strict' is more secure but can break some flows)
        max_age=int(app_token_expires_delta.total_seconds()), # Cookie lifetime
        path="/" # Cookie accessible for all paths
    )
    return response


@router.get("/auth/verify", tags=["auth"])
async def auth_verify(
    # If this dependency resolves without error, the user is authenticated
    current_user: User = Depends(get_current_user)
    ):
    """Checks if the current user's session (cookie) is valid."""
    if current_user:
         logger.info(f"Auth verify successful for user: {current_user.email}")
         # Return user info or just confirmation
         return JSONResponse(status_code=200, content={"detail" : "authenticated", "user_email": current_user.email})
    else:
         # This case should technically be unreachable if Depends works correctly
         logger.error("Auth verify failed unexpectedly (get_current_user returned None?).")
         raise HTTPException(status_code=401, detail="Authentication failed")


@router.get("/logout", tags=["auth"])
async def logout(request: Request):
    """Logs the user out by deleting the session cookie and revoking the Google token."""
    logger.info("Logout requested.")
    access_token_cookie = request.cookies.get("access_token")
    google_token_to_revoke = None

    # Attempt to extract Google token from our JWT for revocation
    if access_token_cookie:
        try:
            # Decode without verification just to get the token
            payload = jwt.decode(access_token_cookie, settings.jwt_secret_key, algorithms=[settings.algorithm], options={"verify_signature": False, "verify_exp": False})
            google_token_to_revoke = payload.get("google_access_token")
        except Exception as e:
            logger.warning(f"Could not decode access token during logout to extract Google token: {e}")

    # Revoke the Google token if found
    if google_token_to_revoke:
        logger.info("Attempting to revoke Google access token...")
        try:
            async with httpx.AsyncClient() as client:
                 revoke_url = "https://oauth2.googleapis.com/revoke"
                 response = await client.post(revoke_url, params={'token': google_token_to_revoke})
                 if response.status_code == 200:
                     logger.info("Google token revoked successfully.")
                 else:
                     # Log failure, but don't block logout
                     logger.warning(f"Failed to revoke Google token: {response.status_code} - {response.text}")
        except Exception as e:
            # Log error, but don't block logout
            logger.error(f"Error during Google token revocation request: {e}", exc_info=True)

    # Clear any server-side session data (if used)
    request.session.clear()

    # Create response and delete the client-side cookie
    response = JSONResponse(content={"message": "Logged out successfully."})
    response.delete_cookie("access_token", path="/") # Ensure path matches where it was set
    logger.info("User logged out, cookie deleted.")
    return response