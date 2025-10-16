from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Callable
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext

from .config import settings
from .models import UserInDB, UserPublic, Role


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# In-memory user storage for demo purposes
_users_by_email: Dict[str, UserInDB] = {}
_refresh_store: Dict[str, str] = {}  # refresh_token -> user_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    expire = _now() + (expires_delta or settings.access_expires)
    to_encode = {"sub": subject, "exp": expire}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    expire = _now() + (expires_delta or settings.refresh_expires)
    jti = str(uuid.uuid4())
    to_encode = {"sub": subject, "jti": jti, "exp": expire, "type": "refresh"}
    token = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    _refresh_store[token] = subject
    return token


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e


def get_user_public(user: UserInDB) -> UserPublic:
    return UserPublic(id=user.id, email=user.email, full_name=user.full_name, role=user.role)


def get_current_user(token: str = Depends(oauth2_scheme)) -> UserInDB:
    payload = decode_token(token)
    subject = payload.get("sub")
    if subject is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    # Try lookup by id first
    user = next((u for u in _users_by_email.values() if u.id == subject), None)
    if user is None:
        # Maybe subject stored email
        user = _users_by_email.get(subject)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_role(required: Role) -> Callable[[UserInDB], UserInDB]:
    def _checker(user: UserInDB = Depends(get_current_user)) -> UserInDB:
        if required == "Dev":
            # Dev endpoints allow Admin too
            if user.role not in ("Dev", "Admin"):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        else:
            if user.role != required:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _checker


def seed_demo_users():
    if _users_by_email:
        return
    admin = UserInDB(
        id=str(uuid.uuid4()),
        email="admin@example.com",
        full_name="Admin User",
        role="Admin",
        hashed_password=hash_password("Admin@123"),
    )
    dev = UserInDB(
        id=str(uuid.uuid4()),
        email="dev@example.com",
        full_name="Dev User",
        role="Dev",
        hashed_password=hash_password("Dev@123"),
    )
    _users_by_email[admin.email] = admin
    _users_by_email[dev.email] = dev


def authenticate(email: str, password: str) -> Optional[UserInDB]:
    user = _users_by_email.get(email.lower())
    if user and verify_password(password, user.hashed_password):
        return user
    return None


def rotate_refresh_token(old_token: str, subject: str) -> str:
    # Invalidate old token
    if old_token in _refresh_store:
        del _refresh_store[old_token]
    return create_refresh_token(subject)


def validate_refresh_token(refresh_token: str) -> str:
    if refresh_token not in _refresh_store:
        # still check signature/expiry to differentiate errors
        try:
            decode_token(refresh_token)
        except HTTPException as e:
            raise e
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid refresh token")
    return _refresh_store[refresh_token]

