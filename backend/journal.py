# -*- coding: utf-8 -*-
"""
Журнал событий аудита (ТЗ §11).

Кто и что сделал: вход, загрузка, решение по проверке, публикация
значений, переиндексация, выгрузка. Тексты документов и пароли в
журнал не попадают — только короткие идентификаторы.

Экспорт в CSV экранирует ячейки, начинающиеся с =, +, -, @: табличные
программы иначе исполняют содержимое как формулу (инъекция CSV).
"""
import csv
import io
import time

from . import db

# Действия, которые пишутся в журнал. Список фиксированный: значение
# action не склеивается из пользовательского ввода.
ACTIONS = ("login", "login_fail", "login_block", "logout",
           "upload", "upload_dup", "decide_accept", "decide_reject",
           "publish", "reindex", "export")


def log(conn, action: str, actor: str | None = None,
        role: str | None = None, entity: str | None = None,
        detail: str | None = None) -> None:
    """Пишет событие. Ошибка записи не должна ронять основное действие."""
    try:
        conn.execute(
            "INSERT INTO audit_log(ts, actor, role, action, entity, detail)"
            " VALUES(?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), actor, role, action,
             entity, detail))
        conn.commit()
    except Exception:
        pass


def query(limit: int = 50, offset: int = 0, action: str | None = None,
          actor: str | None = None) -> list[dict]:
    """Страница журнала с фильтрами, новые сверху."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    sql = ("SELECT id, ts, actor, role, action, entity, detail"
           " FROM audit_log WHERE 1=1")
    args: list = []
    if action:
        sql += " AND action = ?"
        args.append(action)
    if actor:
        sql += " AND actor = ?"
        args.append(actor)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    total = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    conn.close()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def _escape_cell(v) -> str:
    """Снимает формулу с ячейки, если она начинается с опасного знака."""
    if v is None:
        return ""
    s = str(v)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def to_csv(items: list[dict]) -> str:
    """CSV-выгрузка журнала с экранированием формул."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "ts", "actor", "role", "action", "entity", "detail"])
    for it in items:
        w.writerow([_escape_cell(it.get(k)) for k in
                    ("id", "ts", "actor", "role", "action", "entity", "detail")])
    return buf.getvalue()
