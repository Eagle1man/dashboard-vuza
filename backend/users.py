# -*- coding: utf-8 -*-
"""Логины и пароли подразделений.

Зачем отдельная команда. При первом запуске каждому подразделению
выдаётся случайный пароль (см. db.seed_divisions), он один раз
пишется в data/initial_passwords.txt. Раздать пароль людям — дело
администратора, но поменять его на свой он должен уметь без правки
базы вручную и без перезапуска сервера.

    python -m backend.users list                 кто есть и когда заводили
    python -m backend.users passwd admin         спросит новый пароль дважды
    python -m backend.users passwd admin --random  сгенерировать и напечатать
    python -m backend.users passwd admin --password "..."   для скрипта

Пароль не печатается в журнал и не попадает в git: в базе только хэш.
"""
import getpass
import os
import secrets
import sys

from . import auth
from . import config
from . import db
from .console import set_console_utf8


def _conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


def _hash(password: str) -> str:
    if auth.ARGON2:
        return auth._ph.hash(password)
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", password.encode(), b"salt",
                               100_000).hex()


def list_users() -> int:
    conn = _conn()
    rows = conn.execute(
        "SELECT division_id, title, login, created_at FROM division"
        " ORDER BY division_id").fetchall()
    conn.close()
    print(f"Подразделений: {len(rows)}")
    for r in rows:
        print(f"  {r['login']:<16} {r['division_id']:<16} {r['title']}")
    print()
    print("Сменить пароль:  python -m backend.users passwd <логин>")
    return 0


def _ask_password() -> str | None:
    p1 = getpass.getpass("Новый пароль: ")
    if not p1:
        print("Пустой пароль не принимается.")
        return None
    p2 = getpass.getpass("Повторите: ")
    if p1 != p2:
        print("Пароли не совпали.")
        return None
    return p1


def _check_strength(login: str, password: str) -> list[str]:
    problems = []
    if len(password) < 8:
        problems.append("короче 8 символов")
    if password.lower() == login.lower():
        problems.append("совпадает с логином")
    if password.lower() in ("admin", "password", "12345678", "qwerty123"):
        problems.append("входит в список самых частых")
    return problems


def set_password(login: str, password: str | None, random_password: bool,
                 force: bool) -> int:
    conn = _conn()
    row = conn.execute(
        "SELECT division_id, title FROM division WHERE login = ?",
        (login,)).fetchone()
    if not row:
        conn.close()
        print(f"Логина «{login}» нет. Список: python -m backend.users list")
        return 2

    if random_password:
        password = secrets.token_urlsafe(9)
    elif password is None:
        password = _ask_password()
        if password is None:
            conn.close()
            return 2

    problems = _check_strength(login, password)
    if problems and not force:
        conn.close()
        print("Пароль не принят: " + ", ".join(problems) + ".")
        print("  Если так и надо — повторите с --force.")
        return 2

    conn.execute("UPDATE division SET pass_hash = ? WHERE login = ?",
                 (_hash(password), login))
    conn.commit()
    conn.close()

    print(f"Пароль изменён: {login} ({row['title']}).")
    if random_password:
        print(f"  Новый пароль: {password}")
        print("  Сохраните его и передайте владельцу подразделения.")
    print("  Перезапуск сервера не нужен; старые сессии этого логина"
          " продолжают жить до 8 часов.")
    return 0


def main(argv=None) -> int:
    set_console_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)

    password = None
    random_password = False
    force = False
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--password":
            i += 1
            if i >= len(argv):
                print("--password требует значение")
                return 2
            password = argv[i]
        elif a.startswith("--password="):
            password = a.split("=", 1)[1]
        elif a == "--random":
            random_password = True
        elif a == "--force":
            force = True
        else:
            rest.append(a)
        i += 1

    if not rest or rest[0] in ("list", "ls"):
        return list_users()
    if rest[0] == "passwd":
        if len(rest) < 2:
            print("Укажите логин: python -m backend.users passwd <логин>")
            return 2
        return set_password(rest[1], password, random_password, force)

    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
