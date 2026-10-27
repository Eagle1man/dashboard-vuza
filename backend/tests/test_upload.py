# -*- coding: utf-8 -*-
"""
Проверки конвейера загрузки: валидация файла, дубль, анкета, запись числа.

Индексация подменяется фейком (без Ollama): тесту важна оркестрация —
что файл сохранился, анкета легла целиком, число ушло в витрину с
правильным периодом и org_id='ALL'. Векторный поиск проверяется
отдельным E2E на живом сервере.

Запуск: python -m backend.tests.test_upload
"""
import io
import json
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
_OLD_KN = config.KNOWLEDGE_DIR
_OLD_INDEX = uploads.ingest_mod.index_file


def check(name, condition, detail=""):
    if condition:
        print(f"  ОК     {name}")
    else:
        print(f"  ПРОВАЛ {name}  {detail}")
        FAILURES.append(name)


class FakeUpload:
    """Минимальный аналог fastapi.UploadFile."""
    def __init__(self, filename, data: bytes):
        self.filename = filename
        self.file = io.BytesIO(data)


def fake_index_file(conn, path, is_demo=False, force=False):
    """Фейковая индексация: заводит doc-строку, как сделал бы ingest."""
    rel = Path(path).relative_to(config.KNOWLEDGE_DIR).as_posix()
    conn.execute(
        "INSERT INTO doc(rel_path, title, doc_type, sha256, n_pages, n_chunks,"
        " is_demo, added_at) VALUES(?,?,?,?,?,?,?,?)",
        (rel, Path(path).name, ".txt", "feed", 1, 3, 0,
         time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return {"status": "ok", "chunks": 3, "blocks": ["academic"]}


def _setup():
    tmp = Path(tempfile.mkdtemp(prefix="dash_upl_"))
    config.DB_PATH = tmp / "test.db"
    config.KNOWLEDGE_DIR = tmp / "knowledge"
    uploads.ingest_mod.index_file = fake_index_file
    conn = db.connect()
    db.init_db(conn)
    db.seed_metrics(conn)
    db.seed_divisions(conn)
    conn.close()
    return tmp


def _teardown():
    config.DB_PATH = _OLD_DB
    config.KNOWLEDGE_DIR = _OLD_KN
    uploads.ingest_mod.index_file = _OLD_INDEX


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
    _setup()
    data = "Протокол приёма: принято 412 абитуриентов.".encode("utf-8")
    res = uploads.handle_upload("buhgalteria", FakeUpload("otchet.txt", data), _form())
    check("загрузка прошла", res.get("doc_id") is not None, str(res)[:200])
    check("чанки из индексации в ответе", res.get("chunks") == 3, str(res.get("chunks")))
    check("next_expected посчитан (ежеквартально)", bool(res.get("next_expected")),
          str(res.get("next_expected")))

    on_disk = config.KNOWLEDGE_DIR / "buhgalteria" / str(time.strftime("%Y")) / "otchet.txt"
    check("файл сохранён в knowledge/<подразделение>/<год>/", on_disk.exists(), str(on_disk))

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
    check("metric_ids записаны в анкету",
          row is not None and "applicant-2025" in row["metric_ids"],
          row["metric_ids"] if row else "")

    ms = conn.execute(
        "SELECT confirmed FROM metric_source WHERE metric_id='applicant-2025'"
        " AND doc_id = ?", (res["doc_id"],)).fetchone()
    check("метрика связана с документом и подтверждена",
          ms is not None and ms["confirmed"] == 1, str(dict(ms)) if ms else "")

    dm = conn.execute(
        "SELECT source, src_note FROM dim_metric"
        " WHERE metric_id='applicant-2025'").fetchone()
    check("подтверждённое число снимает пометку демо (source=official, src_note=документ)",
          dm is not None and dm["source"] == "official"
          and dm["src_note"] == "Тестовый отчёт",
          str(dict(dm)) if dm else "")

    fm = conn.execute(
        "SELECT f.value FROM fact_metric f JOIN dim_period p ON p.period_id=f.period_id"
        " WHERE f.metric_id='applicant-2025' AND f.org_id='ALL'"
        " AND p.label='I квартал 2026'").fetchone()
    check("число 412 записано в fact_metric с org_id='ALL'",
          fm is not None and abs(fm["value"] - 412) < 0.01,
          str(dict(fm)) if fm else "нет строки")
    conn.close()


def test_duplicate_rejected():
    _setup()
    data = "Один и тот же файл.".encode("utf-8")
    uploads.handle_upload("buhgalteria", FakeUpload("a.txt", data), _form())
    code = _status_code(uploads.handle_upload, "buhgalteria",
                        FakeUpload("b.txt", data), _form())
    check("дубль по содержимому -> 409", code == 409, f"code={code}")


def test_bad_extension_rejected():
    _setup()
    code = _status_code(uploads.handle_upload, "buhgalteria",
                        FakeUpload("virus.exe", b"MZ"), _form())
    check("exe отклонён -> 400", code == 400, f"code={code}")


def test_no_file_rejected():
    _setup()
    code = _status_code(uploads.handle_upload, "buhgalteria",
                        FakeUpload("", b"x"), _form())
    check("пустое имя файла -> 400", code == 400, f"code={code}")


def test_unconfirmed_stays_out_of_vitrine():
    _setup()
    data = "Числа не подтверждены ответственным.".encode("utf-8")
    res = uploads.handle_upload(
        "buhgalteria", FakeUpload("neconfirmed.txt", data),
        _form(confirmed="false", metric_values="{}",
              metric_ids=json.dumps(["vnoko-employers"]),
              period="II квартал 2026"))
    conn = db.connect()
    ms = conn.execute(
        "SELECT confirmed FROM metric_source WHERE metric_id='vnoko-employers'"
        " AND doc_id = ?", (res["doc_id"],)).fetchone()
    check("без подтверждения confirmed=0", ms is not None and ms["confirmed"] == 0,
          str(dict(ms)) if ms else "")
    fm = conn.execute(
        "SELECT f.value FROM fact_metric f JOIN dim_period p ON p.period_id=f.period_id"
        " WHERE f.metric_id='vnoko-employers' AND p.label='II квартал 2026'").fetchone()
    check("неподтверждённое число не пишется в витрину", fm is None,
          str(dict(fm)) if fm else "")
    dm = conn.execute(
        "SELECT source FROM dim_metric WHERE metric_id='vnoko-employers'").fetchone()
    check("неподтверждённая загрузка не снимает пометку демо",
          dm is not None and dm["source"] == "demo",
          str(dict(dm)) if dm else "")
    conn.close()


def test_razovo_has_no_next_expected():
    _setup()
    res = uploads.handle_upload(
        "buhgalteria", FakeUpload("razovo.txt", "Разовый документ.".encode("utf-8")),
        _form(frequency="разово"))
    check("частота «разово» -> next_expected пуст",
          res.get("next_expected") is None, str(res.get("next_expected")))


def test_empty_questionnaire_rejected():
    """Анкета обязательна на сервере, а не только на форме."""
    _setup()
    try:
        uploads.handle_upload(
            "buhgalteria", FakeUpload("a.txt", "текст".encode("utf-8")),
            _form(title="", purpose="", period="", responsible=""))
        check("пустая анкета -> 400", False, "исключение не брошено")
    except Exception as e:  # noqa: BLE001
        check("пустая анкета -> 400", getattr(e, "status_code", None) == 400,
              str(getattr(e, "status_code", None)))
        check("в ошибке перечислены пустые поля",
              "Анкета обязательна" in str(getattr(e, "detail", "")),
              str(getattr(e, "detail", "")))
    try:
        uploads.handle_upload(
            "buhgalteria", FakeUpload("b.txt", "текст-2".encode("utf-8")),
            _form(purpose="   "))
        check("непустая только по названию анкета -> 400", False,
              "исключение не брошено")
    except Exception as e:  # noqa: BLE001
        check("непустая только по названию анкета -> 400",
              getattr(e, "status_code", None) == 400,
              str(getattr(e, "status_code", None)))


def main():
    print("Проверки конвейера загрузки (временная база, фейковая индексация)")
    try:
        test_upload_ok()
        test_duplicate_rejected()
        test_bad_extension_rejected()
        test_no_file_rejected()
        test_empty_questionnaire_rejected()
        test_unconfirmed_stays_out_of_vitrine()
        test_razovo_has_no_next_expected()
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
