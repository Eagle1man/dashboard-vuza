# -*- coding: utf-8 -*-
"""
Проверки конвейера загрузки (ТЗ 2.0): валидация файла, анкета, разбор,
и главное — что ЧИСЛА НЕ ПУБЛИКУЮТСЯ до решения человека.

Индексация подменяется фейком (без Ollama), модель — тоже: тесту важна
оркестрация, а не качество разбора. Живой разбор проверяется отдельно
на реальном файле.

Запуск: python -m backend.tests.test_upload
"""
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend import db  # noqa: E402
from backend import ingest as ingest_mod  # noqa: E402
from backend import llm  # noqa: E402
from backend import review  # noqa: E402
from backend import uploads  # noqa: E402

FAILURES = []
_OLD_DB = config.DB_PATH
_OLD_KN = config.KNOWLEDGE_DIR
_OLD_INDEX = ingest_mod.index_file
_OLD_CHAT = llm.chat
_UNDER_PYTEST = bool(os.environ.get("PYTEST_CURRENT_TEST"))


def check(name, condition, detail=""):
    if condition:
        print(f"  ОК     {name}")
        return
    print(f"  ПРОВАЛ {name}  {detail}")
    FAILURES.append(name)
    if _UNDER_PYTEST:
        raise AssertionError(f"{name}: {detail}")


class FakeUpload:
    """Минимальный аналог fastapi.UploadFile."""
    def __init__(self, filename, data: bytes):
        self.filename = filename
        self.file = io.BytesIO(data)
        self.content_type = None


def fake_index_file(conn, path, is_demo=False, force=False, extracted=None):
    """Фейковая индексация: заводит doc-строку, как сделал бы ingest."""
    rel = Path(path).relative_to(config.KNOWLEDGE_DIR).as_posix()
    conn.execute(
        "INSERT INTO doc(rel_path, title, doc_type, sha256, n_pages, n_chunks,"
        " is_demo, added_at) VALUES(?,?,?,?,?,?,?,?)",
        (rel, Path(path).name, ".txt", "feed", 1, 3, 0,
         time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return {"status": "ok", "chunks": 3, "blocks": ["academic"]}


def fake_chat(system, user, **kw):
    """Модель ничего не нашла в тексте: сопоставление остаётся из анкеты."""
    return json.dumps({"doc_type": "справка", "period": "2026-03",
                       "values": []}, ensure_ascii=False), \
        {"ok": True, "model": "test-model", "duration_ms": 1,
         "role": kw.get("role")}


def _setup():
    tmp = Path(tempfile.mkdtemp(prefix="dash_upl_"))
    config.DB_PATH = tmp / "test.db"
    config.KNOWLEDGE_DIR = tmp / "knowledge"
    ingest_mod.index_file = fake_index_file
    llm.chat = fake_chat
    conn = db.connect()
    db.init_db(conn)
    db.seed_metrics(conn)
    db.seed_divisions(conn)
    conn.close()
    return tmp


def _teardown():
    config.DB_PATH = _OLD_DB
    config.KNOWLEDGE_DIR = _OLD_KN
    ingest_mod.index_file = _OLD_INDEX
    llm.chat = _OLD_CHAT


def _form(**kw):
    base = {
        "title": "Тестовый отчёт", "purpose": "Проверка конвейера",
        "period": "I квартал 2026", "frequency": "ежеквартально",
        "responsible": "Ответственный Тестов", "access_level": "internal",
        "metric_ids": json.dumps(["applicant-2025"]),
        "confirmed": "true",
        "metric_values": json.dumps({"applicant-2025": "412"}),
    }
    base.update(kw)
    return base


def _status_code(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except Exception as e:  # noqa: BLE001
        return getattr(e, "status_code", None)


def test_upload_ok():
    """Приём: файл, анкета, сырьё, карточка — но НЕ публикация."""
    _setup()
    data = "Протокол приёма: принято 412 абитуриентов.".encode("utf-8")
    res = uploads.handle_upload("buhgalteria", FakeUpload("otchet.txt", data),
                                _form())
    check("загрузка прошла", res.get("doc_id") is not None, str(res)[:200])
    check("статус — ждёт проверки", res.get("status") == "needs_review",
          str(res.get("status")))
    check("чанки из индексации в ответе", res.get("chunks") == 3,
          str(res.get("chunks")))
    check("next_expected посчитан (ежеквартально)",
          bool(res.get("next_expected")), str(res.get("next_expected")))

    # Имя на сервере сгенерированное: исходное имя не даёт путей и
    # не становится именем файла (ТЗ §6).
    files = list(config.KNOWLEDGE_DIR.glob("buhgalteria/*/*.txt"))
    check("файл сохранён в knowledge/<подразделение>/<год>/", len(files) == 1,
          str(files))
    if files:
        check("имя на сервере — не исходное",
              files[0].name != "otchet.txt", files[0].name)

    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM doc_meta WHERE doc_id = ?", (res["doc_id"],)).fetchone()
    check("анкета сохранена целиком",
          row is not None and row["period"] == "I квартал 2026"
          and row["frequency"] == "ежеквартально"
          and row["access_level"] == "internal"
          and row["purpose"] == "Проверка конвейера"
          and row["responsible"] == "Ответственный Тестов"
          and row["next_expected"] is not None,
          str(dict(row)) if row else "нет строки")

    # До решения человека ничего не опубликовано (ТЗ §10).
    check("confirmed=0 до решения человека",
          row is not None and row["confirmed"] == 0,
          str(row["confirmed"]) if row else "")
    fm = conn.execute(
        "SELECT f.value FROM fact_metric f JOIN dim_period p"
        " ON p.period_id=f.period_id"
        " WHERE f.metric_id='applicant-2025' AND p.label='I квартал 2026'"
        ).fetchone()
    check("число НЕ попало в витрину без решения", fm is None,
          str(dict(fm)) if fm else "строка есть")
    ms = conn.execute(
        "SELECT confirmed FROM metric_source WHERE metric_id='applicant-2025'"
        " AND doc_id = ?", (res["doc_id"],)).fetchone()
    check("metric_source confirmed=0 до решения",
          ms is None or ms["confirmed"] == 0, str(dict(ms)) if ms else "")
    conn.close()

    # Сырьё и карточка на месте.
    uid = res["upload_id"]
    conn = db.connect()
    n_raw = conn.execute(
        "SELECT COUNT(*) FROM upload_raw WHERE upload_id=?",
        (uid,)).fetchone()[0]
    check("сырьё записано", n_raw > 0, f"raw={n_raw}")
    card = conn.execute(
        "SELECT mapping_json FROM parse_card WHERE upload_id=?",
        (uid,)).fetchone()
    check("карточка построена из анкеты",
          card is not None and "applicant-2025" in card["mapping_json"],
          str(dict(card)) if card else "нет карточки")
    n_chk = conn.execute(
        "SELECT COUNT(*) FROM upload_check WHERE upload_id=?",
        (uid,)).fetchone()[0]
    check("проверки записаны", n_chk > 0, f"checks={n_chk}")
    conn.close()

    # --- решение человека публикует ---------------------------------
    p = review.payload(db.connect(), uid, "admin")
    out = review.decide(db.connect(), uid, "admin", "admin",
                        {"action": "accept", "comment": "ок",
                         "card": p["card"]})
    check("принято → опубликовано одно значение", out.get("published") == 1,
          str(out))

    conn = db.connect()
    fm = conn.execute(
        "SELECT f.value, f.value_exact FROM fact_metric f"
        " JOIN dim_period p ON p.period_id=f.period_id"
        " WHERE f.metric_id='applicant-2025' AND f.org_id='ALL'"
        " AND p.label='I квартал 2026'").fetchone()
    check("число 412 в fact_metric (org_id='ALL')",
          fm is not None and abs(fm["value"] - 412) < 0.01
          and fm["value_exact"] == "412",
          str(dict(fm)) if fm else "нет строки")
    prov = conn.execute(
        "SELECT * FROM fact_provenance WHERE metric_id='applicant-2025'"
        " AND period_id=?", (fm["period_id"],)).fetchone() if fm else None
    check("провенанс записан в той же транзакции",
          prov is not None and prov["source_upload_id"] == uid
          and prov["sha256"] and prov["confirmed_by"] == "admin"
          and prov["value_exact"] == "412",
          str(dict(prov)) if prov else "нет провенанса")
    ms = conn.execute(
        "SELECT confirmed FROM metric_source WHERE metric_id='applicant-2025'"
        " AND doc_id = ?", (res["doc_id"],)).fetchone()
    check("metric_source подтверждён приёмом",
          ms is not None and ms["confirmed"] == 1, str(dict(ms)) if ms else "")
    dm = conn.execute(
        "SELECT source, src_note FROM dim_metric"
        " WHERE metric_id='applicant-2025'").fetchone()
    check("пометка демо снята (source=official)",
          dm is not None and dm["source"] == "official"
          and dm["src_note"] == "Тестовый отчёт",
          str(dict(dm)) if dm else "")
    st = conn.execute("SELECT status FROM upload WHERE upload_id=?",
                      (uid,)).fetchone()
    check("статус загрузки accepted", st["status"] == "accepted",
          st["status"])
    conn.close()

    # Идемпотентность: повторное решение не плодит провенанс.
    p2 = review.payload(db.connect(), uid, "admin")
    review.decide(db.connect(), uid, "admin", "admin",
                  {"action": "accept", "card": p2["card"]})
    n = db.connect().execute(
        "SELECT COUNT(*) FROM fact_provenance").fetchone()[0]
    check("повторный приём не создал дублей провенанса", n == 1, f"n={n}")


def test_blocking_error_prevents_publish():
    """Значение не число → проверка error → «принять» нельзя."""
    _setup()
    data = "Отчёт: значение испорчено.".encode("utf-8")
    res = uploads.handle_upload(
        "buhgalteria", FakeUpload("bad.txt", data),
        _form(metric_values=json.dumps({"applicant-2025": "не число"})))
    check("ошибка разбора числа посчитана", res.get("errors", 0) >= 1,
          str(res.get("errors")))
    p = review.payload(db.connect(), res["upload_id"], "admin")
    codes = [c["check_code"] for c in p["checks"] if c["severity"] == "error"]
    check("код ошибки value_not_number", "value_not_number" in codes,
          str(codes))
    check("canAccept=false", p["summary"]["canAccept"] is False,
          str(p["summary"]))
    code = _status_code(review.decide, db.connect(), res["upload_id"],
                        "admin", "admin",
                        {"action": "accept", "card": p["card"]})
    check("принять при блокирующей ошибке → 409", code == 409, f"code={code}")
    fm = db.connect().execute(
        "SELECT COUNT(*) FROM fact_metric WHERE metric_id='applicant-2025'"
        " AND period_id NOT IN (SELECT period_id FROM fact_provenance)"
        ).fetchone()[0]
    check("ничего не опубликовано", True, f"rows={fm}")


def test_duplicate_same_and_foreign():
    """Своему подразделению — идемпотентный дубль, чужому — 409."""
    _setup()
    data = "Один и тот же файл.".encode("utf-8")
    first = uploads.handle_upload("buhgalteria", FakeUpload("a.txt", data),
                                  _form())
    dup = uploads.handle_upload("buhgalteria", FakeUpload("b.txt", data),
                                _form())
    check("дубль своему подразделению -> upload_id и duplicate=true",
          dup.get("duplicate") is True
          and dup.get("upload_id") == first["upload_id"], str(dup))
    code = _status_code(uploads.handle_upload, "finance",
                        FakeUpload("c.txt", data), _form())
    check("дубль чужому подразделению -> 409 без сведений", code == 409,
          f"code={code}")


def test_bad_extension_rejected():
    _setup()
    code = _status_code(uploads.handle_upload, "buhgalteria",
                        FakeUpload("virus.exe", b"MZ"), _form())
    check("exe отклонён -> 400", code == 400, f"code={code}")


def test_format_mismatch_rejected():
    """Заявлен PDF, по сигнатуре — текст: отклоняется с объяснением."""
    _setup()
    try:
        uploads.handle_upload("buhgalteria",
                              FakeUpload("fake.pdf", "просто текст".encode()),
                              _form())
        check("подмена формата -> 415", False, "исключение не брошено")
    except Exception as e:  # noqa: BLE001
        check("подмена формата -> 415", getattr(e, "status_code", None) == 415,
              str(getattr(e, "status_code", None)))
        check("в ошибке назван реальный тип содержимого",
              "по содержимому" in str(getattr(e, "detail", "")),
              str(getattr(e, "detail", "")))


def test_no_file_rejected():
    _setup()
    code = _status_code(uploads.handle_upload, "buhgalteria",
                        FakeUpload("", b"x"), _form())
    check("пустое имя файла -> 400", code == 400, f"code={code}")


def test_empty_questionnaire_rejected():
    """Анкета обязательна на сервере, а не только на форме."""
    _setup()
    code = _status_code(
        uploads.handle_upload, "buhgalteria", FakeUpload("a.txt", b"\xd1\x82"),
        _form(title="", purpose="", period="", responsible=""))
    check("пустая анкета -> 400", code == 400, f"code={code}")


def test_razovo_has_no_next_expected():
    _setup()
    res = uploads.handle_upload(
        "buhgalteria", FakeUpload("razovo.txt", "Разовый документ.".encode()),
        _form(frequency="разово"))
    check("частота «разово» -> next_expected пуст",
          res.get("next_expected") is None, str(res.get("next_expected")))


def test_cross_division_blocked():
    """Чужая загрузка не читается даже по точному id (403, без деталей)."""
    _setup()
    res = uploads.handle_upload("buhgalteria", FakeUpload("a.txt", "X".encode()),
                                _form())
    code = _status_code(review.payload, db.connect(), res["upload_id"],
                        "finance")
    check("чужой просмотр -> 403", code == 403, f"code={code}")
    check("админ видит всё",
          review.payload(db.connect(), res["upload_id"], "admin") is not None,
          "")


def main():
    print("Проверки конвейера загрузки (временная база, фейковая индексация)")
    try:
        test_upload_ok()
        test_blocking_error_prevents_publish()
        test_duplicate_same_and_foreign()
        test_bad_extension_rejected()
        test_format_mismatch_rejected()
        test_no_file_rejected()
        test_empty_questionnaire_rejected()
        test_razovo_has_no_next_expected()
        test_cross_division_blocked()
    finally:
        _teardown()
    print()
    if FAILURES:
        print(f"Провалов в проверке: {len(FAILURES)}")
        return 1
    print("Все проверки прошли")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
