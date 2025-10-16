from pydantic import BaseModel, EmailStr
from typing import Optional, Literal


Role = Literal["Admin", "Dev"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserPublic(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str] = None
    role: Role


class UserInDB(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str] = None
    role: Role
    hashed_password: str

