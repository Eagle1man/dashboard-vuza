# -*- coding: utf-8 -*-
"""
Проверки входа подразделений: сид, пароли, сессии.

Всё на временной базе в temp-каталоге: тест не трогает
data/dashboard.db и не зависит от состояния установки.

Запуск: python -m backend.tests.test_auth
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import auth  # noqa: E402
from backend import config  # noqa: E402
from backend import db  # noqa: E402

FAILURES = []
_OLD_DB_PATH = config.DB_PATH


def check(name, condition, detail=""):
    if condition:
        print(f"  ОК     {name}")
    else:
        print(f"  ПРОВАЛ {name}  {detail}")
        FAILURES.append(name)


def _fresh_db():
    """Временная база + сид подразделений. Возвращает соединение."""
    tmp = Path(tempfile.mkdtemp(prefix="dash_test_"))
    config.DB_PATH = tmp / "test.db"
    conn = db.connect()
    db.init_db(conn)
    db.seed_divisions(conn)
    return conn


def _passwords() -> dict:
    """Пароли, выданные сидом: логин -> пароль (файл рядом с базой)."""
    path = Path(config.DB_PATH).parent / "initial_passwords.txt"
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] and not parts[0].startswith("Логины"):
            out[parts[0]] = parts[1]
    return out


def test_seed_creates_admin_and_divisions():
    conn = _fresh_db()
    rows = conn.execute("SELECT * FROM division").fetchall()
    ids = {r["division_id"] for r in rows}
    check("admin создан", "admin" in ids, str(sorted(ids)[:5]))
    check("подразделения из оргструктуры посеяны", len(rows) >= 20, f"n={len(rows)}")
    check("логины уникальны",
          len({r["login"] for r in rows}) == len(rows))
    check("все пароли захэшированы, не голым текстом",
          all(r["pass_hash"] != r["login"] and len(r["pass_hash"]) >= 32 for r in rows))
    pw = _passwords()
    check("пароли выданы и записаны рядом с базой",
          len(pw) == len(rows), f"паролей {len(pw)}, учёток {len(rows)}")
    check("пароль не совпадает с логином",
          all(pw.get(r["login"]) != r["login"] for r in rows),
          "пароль по умолчанию равен логину")


def test_verify_login():
    conn = _fresh_db()
    pw = _passwords()
    admin_pw = pw.get("admin", "")
    check("верный пароль admin",
          bool(admin_pw) and auth.verify_login("admin", admin_pw) == "admin")
    check("старый пароль «admin» больше не принимается",
          auth.verify_login("admin", "admin") is None)
    check("неверный пароль отклонён",
          auth.verify_login("admin", "wrong") is None)
    check("подразделение входит под своим паролем",
          auth.verify_login("buhgalteria", pw.get("buhgalteria", ""))
          == "buhgalteria")
    check("пароль-логин подразделения отклонён",
          auth.verify_login("buhgalteria", "buhgalteria") is None)
    check("неизвестный логин отклонён",
          auth.verify_login("nouser", "nouser") is None)
    check("админский флаг", auth.admin_login_ok("admin", admin_pw))
    check("чужой вход — не админ",
          not auth.admin_login_ok("buhgalteria", pw.get("buhgalteria", "")))


def test_seed_does_not_overwrite_changed_password():
    """Смена пароля должна жить после перезапуска сервера.

    Сид вызывается при каждом старте. Пока в нём стоял INSERT OR REPLACE,
    пароль возвращался к прежнему на каждом запуске — то есть смена
    пароля не работала вовсе.
    """
    conn = _fresh_db()
    new_hash = auth._ph.hash("Novyy-Parol-2026") if auth.ARGON2 else "x" * 64
    conn.execute("UPDATE division SET pass_hash = ? WHERE login = 'admin'",
                 (new_hash,))
    conn.commit()
    db.seed_divisions(conn)
    check("пароль после повторного сида прежний",
          auth.verify_login("admin", "Novyy-Parol-2026") == "admin")
    check("пароль из файла после сида не подходит",
          auth.verify_login("admin", _passwords().get("admin", "")) is None
          or not _passwords().get("admin"))


def test_sessions():
    conn = _fresh_db()
    tok = auth.create_session("buhgalteria")
    check("сессия выдаёт division_id",
          auth.validate_session(tok) == "buhgalteria")
    check("чужой токен отклонён", auth.validate_session("no-such-token") is None)
    check("пустой токен отклонён", auth.validate_session(None) is None)
    auth.delete_session(tok)
    check("после logout токен мёртв", auth.validate_session(tok) is None)

    # Просроченная сессия не работает.
    tok2 = auth.create_session("admin")
    auth._SESSIONS[tok2]["expires"] = time.time() - 1
    check("просроченная сессия отклонена", auth.validate_session(tok2) is None)
    check("просроченная сессия удалена из памяти", tok2 not in auth._SESSIONS)


def test_require_auth():
    from fastapi import HTTPException
    try:
        auth.require_auth(None)
        check("require_auth без входа — 401", False, "исключение не брошено")
    except HTTPException as e:
        check("require_auth без входа — 401", e.status_code == 401, str(e.status_code))
    check("require_auth с входом возвращает division_id",
          auth.require_auth("admin") == "admin")


def main():
    print("Проверки входа подразделений (временная база)")
    try:
        test_seed_creates_admin_and_divisions()
        test_verify_login()
        test_seed_does_not_overwrite_changed_password()
        test_sessions()
        test_require_auth()
    finally:
        config.DB_PATH = _OLD_DB_PATH
        auth._SESSIONS.clear()
    print()
    if FAILURES:
        print(f"Провалов в проверке: {len(FAILURES)}")
        return 1
    print("Все проверки прошли")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
