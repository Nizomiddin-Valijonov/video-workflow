"""Auth API: ro'yxatdan o'tish, kirish, token."""
import json
import os
import tempfile

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from app.auth import create_access_token, hash_password, verify_password
from app.core.config import settings

router = APIRouter(tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


# Foydalanuvchilar faylda saqlanadi — backend qayta ishga tushganda
# hisoblar yo'qolib qolmasligi uchun. Parollar passlib bilan hashlanadi.
_USERS_FILE = os.path.join(settings.STORAGE_DIR, "users.json")


def _load_users() -> dict[str, dict]:
    try:
        with open(_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                str(email): user
                for email, user in data.items()
                if isinstance(user, dict) and "password" in user
            }
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {}


def _save_users() -> None:
    directory = os.path.dirname(_USERS_FILE)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".users-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_USERS, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _USERS_FILE)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


_USERS: dict[str, dict] = _load_users()


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn):
    if payload.email in _USERS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Foydalanuvchi allaqachon ro'yxatdan o'tgan")
    _USERS[payload.email] = {"email": payload.email, "password": hash_password(payload.password)}
    _save_users()
    token = create_access_token(payload.email)
    return TokenOut(access_token=token)


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn):
    user = _USERS.get(payload.email)
    if not user or not verify_password(payload.password, user["password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email yoki parol noto'g'ri")
    token = create_access_token(payload.email)
    return TokenOut(access_token=token)
