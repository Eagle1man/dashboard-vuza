# -*- coding: utf-8 -*-
"""
Проверки свежести данных: расчёт даты следующей загрузки и статус
«просрочено» в списке загрузок.

Свежесть — единственный сигнал, что данные устарели: частота
обещана подразделением в анкете, а дата ожидания считается из неё.

Запуск: python -m backend.tests.test_freshness
"""
import datetime
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend import db  # noqa: E402
from backend import uploads  # noqa: E402

FAILURES = []
_OLD_DB = config.DB_PATH


def check(name, condition, detail=""):
    if condition:
        print(f"  ОК     {name}")
    else:
        print(f"  ПРОВАЛ {name}  {detail}")
        FAILURES.append(name)


def _setup():
    tmp = Path(tempfile.mkdtemp(prefix="dash_fresh_"))
    config.DB_PATH = tmp / "test.db"
    conn = db.connect()
    db.init_db(conn)
    db.seed_divisions(conn)
    conn.close()
    return tmp


def _add_upload(conn, filename, next_expected, status="ok"):
    """Вставляет загрузку с анкетой напрямую — без файлов и индексации."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO upload(division_id, filename, rel_path, sha256, size_bytes,"
        " uploaded_at, status) VALUES(?,?,?,?,?,?,?)",
        ("buhgalteria", filename, f"buhgalteria/2026/{filename}",
         filename, 10, now, status))
    # doc_id в doc_meta — FK: документа нет, но внешний ключ можно
    # оставить NULL только в upload; doc_meta.doc_id первичен и не NULL,
    # поэтому заводим минимальную doc-строку.
    conn.execute(
        "INSERT INTO doc(rel_path, title, added_at) VALUES(?,?,?)",
        (f"buhgalteria/2026/{filename}", filename, now))
    doc_id = conn.execute(
        "SELECT doc_id FROM doc WHERE rel_path = ?",
        (f"buhgalteria/2026/{filename}",)).fetchone()["doc_id"]
    conn.execute("UPDATE upload SET doc_id = ? WHERE upload_id = ?",
                 (doc_id, cur.lastrowid))
    conn.execute(
        "INSERT INTO doc_meta(doc_id, upload_id, division_id, title, purpose,"
        " period, frequency, responsible, access_level, next_expected,"
        " metric_ids, confirmed, uploaded_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, cur.lastrowid, "buhgalteria", filename, "цель",
         "I квартал 2026", "ежеквартально", "Ответственный", "internal",
         next_expected, "[]", 0, now))
    conn.commit()
    return cur.lastrowid


def test_next_expected_calc():
    today = datetime.date.today()
    check("разово -> None", db.next_expected_for("разово") is None)
    check("неизвестная частота -> None", db.next_expected_for("иногда") is None)
    d = db.next_expected_for("ежемесячно")
    check("ежемесячно -> +30 дней",
          d == (today + datetime.timedelta(days=30)).isoformat(), str(d))
    d = db.next_expected_for("ежеквартально")
    check("ежеквартально -> +90 дней",
          d == (today + datetime.timedelta(days=90)).isoformat(), str(d))
    d = db.next_expected_for("ежегодно")
    check("ежегодно -> +365 дней",
          d == (today + datetime.timedelta(days=365)).isoformat(), str(d))
    d = db.next_expected_for("раз в семестр")
    check("раз в семестр -> +180 дней",
          d == (today + datetime.timedelta(days=180)).isoformat(), str(d))


def test_list_freshness():
    _setup()
    conn = db.connect()
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    _add_upload(conn, "old.txt", past)
    _add_upload(conn, "fresh.txt", future)
    _add_upload(conn, "onetime.txt", None)

    rows = {r["filename"]: r for r in uploads.list_uploads("buhgalteria")}
    check("просроченная загрузка помечена overdue",
          rows.get("old.txt", {}).get("freshness") == "overdue",
          str(rows.get("old.txt", {}).get("freshness")))
    check("свежая загрузка — ok",
          rows.get("fresh.txt", {}).get("freshness") == "ok",
          str(rows.get("fresh.txt", {}).get("freshness")))
    check("без частоты (next_expected пуст) — ok, а не ложная тревога",
          rows.get("onetime.txt", {}).get("freshness") == "ok",
          str(rows.get("onetime.txt", {}).get("freshness")))
    check("список отдаёт анкету (purpose, period)",
          rows.get("old.txt", {}).get("period") == "I квартал 2026",
          str(rows.get("old.txt", {})))
    conn.close()


def test_list_all_and_by_division():
    _setup()
    conn = db.connect()
    _add_upload(conn, "only.txt", None)
    check("фильтр по подразделению",
          len(uploads.list_uploads("buhgalteria")) == 1)
    check("другое подразделение — пусто",
          len(uploads.list_uploads("career")) == 0)
    check("без фильтра — все",
          len(uploads.list_uploads(None)) == 1)
    conn.close()


def main():
    print("Проверки свежести данных (временная база)")
    try:
        test_next_expected_calc()
        test_list_freshness()
        test_list_all_and_by_division()
    finally:
        config.DB_PATH = _OLD_DB
    print()
    if FAILURES:
        print(f"Провалов в проверке: {len(FAILURES)}")
        return 1
    print("Все проверки прошли")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())