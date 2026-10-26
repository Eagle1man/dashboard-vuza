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


def test_verify_login():
    conn = _fresh_db()
    check("верный пароль admin",
          auth.verify_login("admin", "admin") == "admin")
    check("неверный пароль отклонён",
          auth.verify_login("admin", "wrong") is None)
    check("подразделение входит под своим логином",
          auth.verify_login("buhgalteria", "buhgalteria") == "buhgalteria")
    check("неизвестный логин отклонён",
          auth.verify_login("nouser", "nouser") is None)
    check("админский флаг", auth.admin_login_ok("admin", "admin"))
    check("чужой вход — не админ", not auth.admin_login_ok("buhgalteria", "buhgalteria"))


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
