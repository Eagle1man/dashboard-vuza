# -*- coding: utf-8 -*-
"""
Загрузки подразделений: обёртка над конвейером приёма.

Вся работа по приёму файла (анкета, безопасное сохранение, проверка
формата, извлечение сырья, карточка разбора, детерминированные
проверки) — в backend/intake.py. Здесь — авторизация и проверка, что
подразделение существует, плюс чтение списка/деталей загрузок.

Публикация чисел из конвейера убрана (ТЗ §10): загрузка создаёт
задачу проверки со статусом needs_review, а значения уходят в витрину
только после решения человека на экране проверки (backend/review.py).
"""
import json
import time
from pathlib import Path

from fastapi import HTTPException, UploadFile

from . import auth
from . import db
from . import intake

MAX_FILE_MB = 50


def _ensure_period(conn, period_text: str) -> str:
    """Гарантирует наличие периода в dim_period и возвращает period_id."""
    row = conn.execute(
        "SELECT period_id FROM dim_period WHERE label = ?", (period_text,))
    row = row.fetchone()
    if row:
        return row["period_id"]
    # Генерируем period_id из текста: берём первые 7 символов вида YYYY-MM,
    # иначе — хэш.
    import hashlib
    import re
    m = re.search(r"(\d{4})[-/](\d{1,2})", period_text)
    if m:
        pid = f"{m.group(1)}-{int(m.group(2)):02d}"
    else:
        pid = "period-" + hashlib.sha256(period_text.encode()).hexdigest()[:12]
    # ord — большое число, чтобы не перекрыть существующие месяцы.
    conn.execute(
        "INSERT OR REPLACE INTO dim_period(period_id, ord, label, year, month)"
        " VALUES(?,?,?,?,?)",
        (pid, 9999, period_text, int(m.group(1)) if m else 2026,
         int(m.group(2)) if m else 1))
    conn.commit()
    return pid


def handle_upload(division_id: str, file: UploadFile, form: dict) -> dict:
    """Принимает загрузку от подразделения.

    form: title, purpose, period, frequency, responsible, access_level,
    metric_ids (JSON), confirmed (bool), metric_values (JSON).
    """
    division_id = auth.require_auth(division_id)
    row = db.connect().execute(
        "SELECT division_id FROM division WHERE division_id = ?",
        (division_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Подразделение не найдено")
    return intake.receive(division_id, division_id, file, form)


def list_uploads(division_id: str | None = None) -> list[dict]:
    """Список загрузок со статусом свежести."""
    c = db.connect()
    if division_id:
        rows = c.execute(
            """SELECT u.*, d.title AS division_title, dm.title, dm.purpose, dm.period,
                      dm.frequency, dm.responsible, dm.access_level,
                      dm.next_expected, dm.confirmed, dm.uploaded_at AS meta_uploaded
               FROM upload u
               JOIN division d ON d.division_id = u.division_id
               LEFT JOIN doc_meta dm ON dm.doc_id = u.doc_id
               WHERE u.division_id = ?
               ORDER BY u.uploaded_at DESC""", (division_id,)).fetchall()
    else:
        rows = c.execute(
            """SELECT u.*, d.title AS division_title, dm.title, dm.purpose, dm.period,
                      dm.frequency, dm.responsible, dm.access_level,
                      dm.next_expected, dm.confirmed, dm.uploaded_at AS meta_uploaded
               FROM upload u
               JOIN division d ON d.division_id = u.division_id
               LEFT JOIN doc_meta dm ON dm.doc_id = u.doc_id
               ORDER BY u.uploaded_at DESC""").fetchall()

    now = time.time()
    out = []
    for r in rows:
        status = r["status"]
        fresh = "ok"
        nexp = r["next_expected"]
        if nexp:
            try:
                if time.mktime(time.strptime(nexp, "%Y-%m-%d")) < now:
                    fresh = "overdue"
            except (ValueError, TypeError):
                pass
        # Сколько проверок висит на загрузке — сразу видно в списке.
        n_err = c.execute(
            "SELECT COUNT(*) FROM upload_check WHERE upload_id = ?"
            " AND severity = 'error'", (r["upload_id"],)).fetchone()[0]
        n_warn = c.execute(
            "SELECT COUNT(*) FROM upload_check WHERE upload_id = ?"
            " AND severity = 'warning'", (r["upload_id"],)).fetchone()[0]
        out.append({
            "upload_id": r["upload_id"],
            "division_id": r["division_id"],
            "division_title": r["division_title"],
            "filename": r["filename"],
            "original_name": r["original_name"] or r["filename"],
            "rel_path": r["rel_path"],
            "sha256": r["sha256"],
            "size_bytes": r["size_bytes"],
            "uploaded_at": r["uploaded_at"],
            "status": status,
            "errors": n_err,
            "warnings": n_warn,
            "error": r["error"],
            "title": r["title"],
            "purpose": r["purpose"],
            "period": r["period"],
            "frequency": r["frequency"],
            "responsible": r["responsible"],
            "access_level": r["access_level"],
            "next_expected": r["next_expected"],
            "confirmed": bool(r["confirmed"]),
            "freshness": fresh,
        })
    c.close()
    return out


def get_upload_detail(upload_id: int) -> dict | None:
    """Детали одной загрузки."""
    c = db.connect()
    r = c.execute(
        """SELECT u.*, d.title AS division_title, dm.title, dm.purpose, dm.period,
                  dm.frequency, dm.responsible, dm.access_level,
                  dm.next_expected, dm.metric_ids, dm.confirmed
           FROM upload u
           JOIN division d ON d.division_id = u.division_id
           LEFT JOIN doc_meta dm ON dm.doc_id = u.doc_id
           WHERE u.upload_id = ?""", (upload_id,)).fetchone()
    c.close()
    if not r:
        return None
    return dict(r)
