# -*- coding: utf-8 -*-
"""
Аутентификация подразделений на локальном сервере дашборда (ТЗ §12).

Пароли:
- хранение — Argon2id (argon2-cffi), соль генерируется на каждый хэш;
- старые хэши PBKDF2 (соль «dashboard-<id>») распознаются и при первом
  успешном входе переводятся на Argon2 — после обновления никто не
  теряет доступ, а повторный вход не переписывает хэш заново;
- сравнение через hmac.compare_digest (защита от тайминга).

Сессии:
- токен — случайная строка, cookie HttpOnly + SameSite=Lax;
- хранятся в памяти, TTL 8 часов; перезапуск сервера обнуляет сессии
  (для единичного локального экземпляра это приемлемо);
- отзыв — удаление токена (logout).

Лимит попыток (ТЗ §12):
- после LOGIN_MAX_ATTEMPTS неудач за LOGIN_WINDOW_MIN минут по одному
  логину вход блокируется с явным сообщением и записью login_block;
- выключается RECTOR_LOGIN_LIMIT=0 — решение остаётся за владельцем
  (ранее владелец просил лимит не ставить; ТЗ требует, поэтому
  включено по умолчанию и настраивается).
"""
import hashlib
import hmac
import secrets
import time

from . import config
from . import db

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import (
        InvalidHashError, VerificationError, VerifyMismatchError)
    # Параметры: 64 МиБ памяти, 2 итерации — при них Argon2id считается
    # за десятые доли секунды, но аппаратный перебор паролей становится
    # экономически бессмысленным.
    _ph = PasswordHasher(time_cost=2, memory_cost=64 * 1024,
                         parallelism=2)
    ARGON2 = True
except ImportError:      # pragma: no cover - аргон2 ставится install.bat
    ARGON2 = False

# Сессии в памяти: token -> {"division_id": str, "expires": float}
_SESSIONS: dict[str, dict] = {}
SESSION_TTL = 8 * 60 * 60  # 8 часов

_PBKDF2_ITERS = 100_000


def _legacy_hash(password: str, division_id: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(),
        f"dashboard-{division_id}".encode(), _PBKDF2_ITERS).hex()


def verify_login(login: str, password: str) -> str | None:
    """Проверяет логин/пароль. Возвращает division_id или None."""
    if not login or not password:
        return None
    c = db.connect()
    row = c.execute(
        "SELECT division_id, pass_hash FROM division WHERE login = ?",
        (login,)).fetchone()
    if not row:
        # Фиктивная проверка, чтобы неудача с несуществующим логином
        # не была быстрее неудачи с неверным паролем.
        hmac.compare_digest(_legacy_hash(password, "nobody"), "0" * 64)
        c.close()
        return None

    division_id = row["division_id"]
    stored = row["pass_hash"] or ""

    if stored.startswith("$argon2"):
        if not ARGON2:
            c.close()
            return None
        try:
            ok = _ph.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        c.close()
        return division_id if ok else None

    # Старый формат PBKDF2.
    if not hmac.compare_digest(_legacy_hash(password, division_id), stored):
        c.close()
        return None

    # Апгрейд хэша при первом успешном входе.
    if ARGON2:
        try:
            c.execute("UPDATE division SET pass_hash = ? WHERE division_id = ?",
                      (_ph.hash(password), division_id))
            c.commit()
        except Exception:
            pass
    c.close()
    return division_id


def _cutoff() -> str:
    """Момент начала окна лимита попыток (локальное время, тот же формат,
    что и в колонке ts — иначе сравнение строк не отфильтрует старое)."""
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time()
                                        - config.LOGIN_WINDOW_MIN * 60))


def login_blocked(login: str) -> str | None:
    """Причина блокировки входа или None. ТЗ §12: ограничение попыток."""
    if not config.LOGIN_LIMIT_ON or not login:
        return None
    c = db.connect()
    n = c.execute(
        "SELECT COUNT(*) FROM login_attempt"
        " WHERE login = ? AND ok = 0 AND ts > ?",
        (login, _cutoff())).fetchone()[0]
    c.close()
    if n >= config.LOGIN_MAX_ATTEMPTS:
        return (f"Вход временно заблокирован: {n} неудачных попыток за "
                f"{config.LOGIN_WINDOW_MIN} мин. Повторите позже или "
                "обратитесь к администратору.")
    return None


def record_login(login: str, ip: str | None, ok: bool) -> None:
    """Пишет попытку входа и чистит записи вне окна. Ошибка не роняет вход."""
    try:
        c = db.connect()
        c.execute("DELETE FROM login_attempt WHERE ts <= ?", (_cutoff(),))
        c.execute(
            "INSERT INTO login_attempt(ts, login, ip, ok) VALUES(?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), (login or "")[:80],
             (ip or "")[:64], 1 if ok else 0))
        c.commit()
        c.close()
    except Exception:
        pass


def clear_attempts(login: str) -> None:
    """Успех снимает блокировку."""
    try:
        c = db.connect()
        c.execute("DELETE FROM login_attempt WHERE login = ? AND ok = 0",
                  (login or "")[:80])
        c.commit()
        c.close()
    except Exception:
        pass


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
