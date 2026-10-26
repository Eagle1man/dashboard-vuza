# -*- coding: utf-8 -*-
"""
Аутентификация подразделений на локальном сервере дашборда.

Простая схема, подходящая для работы внутри вуза:
- логин + пароль проверяются против таблицы division в SQLite;
- пароль хранится как PBKDF2-хэш (sha256, 100k итераций), never plain text;
- после успешного входа выдаётся сессионный токен (случайная строка),
  который клиент присылает в cookie `dashboard_session`;
- сервер хранит сессии в памяти с TTL 8 часов.

Нет отдельной БД сессий — сервер локальный, перезапуск обнуляет сессии,
и пользователю достаточно zaloginться снова. Это приемлемо для
единичного экземпляра дашборда в отделе.
"""
import secrets
import time
from pathlib import Path

from . import db
from . import config

# Сессии в памяти: token -> {"division_id": str, "expires": float}
_SESSIONS: dict[str, dict] = {}
SESSION_TTL = 8 * 60 * 60  # 8 часов


def verify_login(login: str, password: str) -> str | None:
    """Проверяет логин/пароль. Возвращает division_id или None."""
    c = db.connect()
    row = c.execute(
        "SELECT division_id, pass_hash FROM division WHERE login = ?", (login,))
    row = row.fetchone()
    if not row:
        return None
    import hashlib
    expected = row["pass_hash"]
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), f"dashboard-{row['division_id']}".encode(),
        100_000).hex()
    if actual != expected:
        return None
    return row["division_id"]


def create_session(division_id: str) -> str:
    """Создаёт сессию и возвращает токен."""
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {
        "division_id": division_id,
        "expires": time.time() + SESSION_TTL,
    }
    return token


def validate_session(token: str | None) -> str | None:
    """Возвращает division_id для валидного токена, иначе None."""
    if not token:
        return None
    sess = _SESSIONS.get(token)
    if not sess:
        return None
    if sess["expires"] < time.time():
        del _SESSIONS[token]
        return None
    return sess["division_id"]


def delete_session(token: str | None):
    if token and token in _SESSIONS:
        del _SESSIONS[token]


def current_division_id() -> str | None:
    """Заглушка для совместимости с кодом, который может позвать без запроса."""
    return None


def require_auth(division_id: str | None) -> str:
    """Проверяет, что пользователь авторизован, и возвращает division_id."""
    if not division_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Необходимо войти")
    return division_id


def admin_login_ok(login: str, password: str) -> bool:
    """Проверка админского логина (отдельная функция для ясности)."""
    return verify_login(login, password) == "admin"
