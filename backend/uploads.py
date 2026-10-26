# -*- coding: utf-8 -*-
"""
Приём загрузок от подразделений.

Поток:
1. Проверка сессии — кто загружает.
2. Проверка файла: тип, размер, дубликат (sha256).
3. Сохранение в knowledge/<division>/<год>/.
4. Индексация через backend/index_file (текст → чанки → векторы).
5. Запись в upload, doc, doc_meta — анкета полностью сохраняется.
6. Если выбраны карточки дашборда и подтверждены числа:
   - metric_source связывает метрику с документом;
   - fact_metric записывает значение за период.

Конфиденциальные файлы (access_level='confidential') попадают в RAG
совместно с остальными — это Agreement с заказчиком.
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

from fastapi import HTTPException, UploadFile

from . import auth
from . import chunking
from . import config
from . import db
from . import embed
from . import extract
from . import ingest as ingest_mod

MAX_FILE_MB = 50


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _ensure_period(conn, period_text: str) -> str:
    """Гарантирует наличие периода в dim_period и возвращает period_id."""
    row = conn.execute(
        "SELECT period_id FROM dim_period WHERE label = ?", (period_text,))
    row = row.fetchone()
    if row:
        return row["period_id"]
    # Генерируем period_id из текста: берём первые 7 символов вида YYYY-MM,
    # иначе — хэш.
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
    return pid


def handle_upload(division_id: str, file: UploadFile, form: dict) -> dict:
    """
    form содержит:
      title, purpose, period, frequency, responsible,
      access_level, metric_ids (JSON), confirmed (bool),
      metric_values (JSON: {metric_id: value})
    """
    # --- 1. Проверка сессии -------------------------------------------
    division_id = auth.require_auth(division_id)
    row = db.connect().execute(
        "SELECT division_id FROM division WHERE division_id = ?",
        (division_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Подразделение не найдено")

    # --- 2. Проверка файла --------------------------------------------
    if not file.filename:
        raise HTTPException(status_code=400, detail="Файл не выбран")
    ext = Path(file.filename).suffix.lower()
    if ext not in config.SUPPORTED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый тип файла: {ext}. "
                   f"Разрешено: {', '.join(sorted(config.SUPPORTED_EXT))}")

    contents = file.file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        raise HTTPException(status_code=413,
                            detail=f"Файл слишком велик: {size_mb:.1f} МБ "
                                   f"(лимит {MAX_FILE_MB} МБ)")

    # --- 3. Сохранение файла ------------------------------------------
    year = time.strftime("%Y")
    save_dir = config.KNOWLEDGE_DIR / division_id / year
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    save_path = save_dir / safe_name

    # Проверка на дубликат по содержимому (до записи на диск).
    tmp_hash = hashlib.sha256(contents).hexdigest()
    existing = db.connect().execute(
        "SELECT upload_id, rel_path FROM upload WHERE sha256 = ?",
        (tmp_hash,)).fetchone()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Такой файл уже загружен ранее: {existing['rel_path']}")

    with save_path.open("wb") as f:
        f.write(contents)

    rel_path = f"{division_id}/{year}/{safe_name}"

    # --- 4. Индексация ------------------------------------------------
    try:
        res = ingest_mod.index_file(
            db.connect(), save_path, is_demo=False, force=True)
    except Exception as exc:
        # Удаляем файл, если индексация не удалась.
        try:
            save_path.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=500,
                            detail=f"Не удалось проиндексировать файл: {exc}")

    if res["status"] in ("empty", "no-chunks"):
        try:
            save_path.unlink()
        except OSError:
            pass
        raise HTTPException(
            status_code=400,
            detail=f"В файле не найдено текста для индексации ({res['status']})")

    doc_id = db.connect().execute(
        "SELECT doc_id FROM doc WHERE rel_path = ?", (rel_path,)).fetchone()["doc_id"]

    # --- 5. Запись загрузки и метаданных ------------------------------
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    title = form.get("title") or safe_name
    purpose = form.get("purpose", "")
    period = form.get("period", now[:7])
    frequency = form.get("frequency", "разово")
    responsible = form.get("responsible", "")
    access_level = form.get("access_level", "open")
    metric_ids_raw = form.get("metric_ids", "[]")
    confirmed = form.get("confirmed", "false").lower() == "true"

    try:
        metric_ids = json.loads(metric_ids_raw) if metric_ids_raw else []
    except json.JSONDecodeError:
        metric_ids = []

    # next_expected по частоте.
    next_exp = db.next_expected_for(frequency)

    c = db.connect()
    cur = c.execute(
        "INSERT INTO upload(division_id, doc_id, filename, rel_path, sha256,"
        " size_bytes, uploaded_at, status) VALUES(?,?,?,?,?,?,?,?)",
        (division_id, doc_id, safe_name, rel_path, tmp_hash, len(contents),
         now, "ok"))

    c.execute(
        "INSERT OR REPLACE INTO doc_meta(doc_id, upload_id, division_id, title,"
        " purpose, period, frequency, responsible, access_level, next_expected,"
        " metric_ids, confirmed, uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, int(cur.lastrowid), division_id, title, purpose, period,
         frequency, responsible, access_level, next_exp,
         json.dumps(metric_ids, ensure_ascii=False), int(confirmed), now))

    # --- 6. metric_source + fact_metric -------------------------------
    metric_values_raw = form.get("metric_values", "{}")
    try:
        metric_values = json.loads(metric_values_raw) if metric_values_raw else {}
    except json.JSONDecodeError:
        metric_values = {}

    if confirmed:
        pid = _ensure_period(c, period)
        for mid in metric_ids:
            c.execute(
                "INSERT OR REPLACE INTO metric_source(metric_id, doc_id, confirmed)"
                " VALUES(?,?,1)", (mid, doc_id))
            val = metric_values.get(mid)
            if val is not None:
                try:
                    val_f = float(val)
                except (TypeError, ValueError):
                    val_f = None
                if val_f is not None:
                    c.execute(
                        "INSERT OR REPLACE INTO fact_metric"
                        "(period_id, metric_id, org_id, value, status)"
                        " VALUES(?,?,?,?,?)",
                        # Значение показателя относится к университету в целом.
                        # Разбивка по кафедрам/академиям появляется только там,
                        # где подразделение загружает именно такую витрину.
                        (pid, mid, "ALL", val_f, "none"))
                    # Подтверждённое ответственным число снимает с показателя
                    # пометку демо: цифра теперь от документа подразделения,
                    # а не из сиженной витрины. Без этого карточка показывает
                    # реальное число, а системный флаг продолжает кричать «демо».
                    c.execute(
                        "UPDATE dim_metric SET source='official', src_note=?"
                        " WHERE metric_id=?", (title, mid))
    else:
        for mid in metric_ids:
            c.execute(
                "INSERT OR REPLACE INTO metric_source(metric_id, doc_id, confirmed)"
                " VALUES(?,?,0)", (mid, doc_id))

    c.commit()

    return {
        "upload_id": int(cur.lastrowid),
        "doc_id": doc_id,
        "rel_path": rel_path,
        "filename": safe_name,
        "title": title,
        "chunks": res["chunks"],
        "blocks": res.get("blocks", []),
        "next_expected": next_exp,
        "confirmed": confirmed,
    }


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
        out.append({
            "upload_id": r["upload_id"],
            "division_id": r["division_id"],
            "division_title": r["division_title"],
            "filename": r["filename"],
            "rel_path": r["rel_path"],
            "sha256": r["sha256"],
            "size_bytes": r["size_bytes"],
            "uploaded_at": r["uploaded_at"],
            "status": r["status"],
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
    if not r:
        return None
    return dict(r)
