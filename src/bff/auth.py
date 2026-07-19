from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyCookie
from jose import JWTError, jwt
from bff.config import (
    JWT_SECRET_KEY, 
    JWT_ALGORITHM, 
    ACCESS_TOKEN_EXPIRE_MINUTES,
    BFF_ADMIN_USER,
    BFF_ADMIN_PASSWORD
)
from bff.schemas import UserLogin, Token
from bff.utils.logging import logger

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# Setup cookie extractor for OpenAPI documentation auto-discovery
cookie_sec = APIKeyCookie(name="session_id", auto_error=False)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Helper to generate signed JWT token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def get_current_user_token(request: Request) -> str:
    """
    Dependency that extracts the session cookie from the frontend,
    decodes it, and returns the token string if valid.
    """
    token = request.cookies.get("session_id")
    if not token:
        # Fallback to Authorization header if testing in Swagger / direct API requests
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Missing session cookie.",
        )
    
    try:
        # Verify and decode JWT
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token content."
            )
        return token
    except JWTError as e:
        logger.warning(f"Failed JWT verification: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid token."
        )

@router.post("/login")
async def login(response: Response, credentials: UserLogin):
    """
    Verifies user credentials, generates a JWT, and sets it
    as a secure HTTP-Only cookie 'session_id'.
    """
    if credentials.username != BFF_ADMIN_USER or credentials.password != BFF_ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    # Generate token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": credentials.username}, 
        expires_delta=access_token_expires
    )
    
    # Set secure HttpOnly cookie
    response.set_cookie(
        key="session_id",
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        expires=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=False  # Set to True in production with HTTPS
    )
    
    logger.info(f"User '{credentials.username}' logged in successfully.")
    return {"message": "Successfully logged in"}

@router.post("/logout")
async def logout(response: Response, token: str = Depends(get_current_user_token)):
    """Clears the session cookie, logging the user out."""
    response.delete_cookie(key="session_id", samesite="lax")
    logger.info("User logged out successfully.")
    return {"message": "Successfully logged out"}
