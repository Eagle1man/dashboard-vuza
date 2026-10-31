# -*- coding: utf-8 -*-
"""
Проверки детерминированной части конвейера: числа, суммы, форматы,
журнал, лимит входа, точность доступности моделей.

Всё без Ollama и без боевой базы: либо чистые функции, либо временная
база в temp-каталоге.

Запуск: python -m backend.tests.test_pipeline
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import auth          # noqa: E402
from backend import checks        # noqa: E402
from backend import config        # noqa: E402
from backend import db            # noqa: E402
from backend import ingest as ingest_mod  # noqa: E402
from backend import intake        # noqa: E402
from backend import journal       # noqa: E402
from backend import llm           # noqa: E402
from backend import numeric       # noqa: E402
from backend import review        # noqa: E402
from fastapi import HTTPException  # noqa: E402

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


def _fresh_db():
    tmp = Path(tempfile.mkdtemp(prefix="dash_pipe_"))
    config.DB_PATH = tmp / "test.db"
    config.KNOWLEDGE_DIR = tmp / "knowledge"
    conn = db.connect()
    db.init_db(conn)
    db.seed_metrics(conn)
    db.seed_divisions(conn)
    return conn, tmp


def _teardown():
    config.DB_PATH = _OLD_DB
    config.KNOWLEDGE_DIR = _OLD_KN
    ingest_mod.index_file = _OLD_INDEX
    llm.chat = _OLD_CHAT


# --------------------------------------------------------------------
def test_numeric():
    cases = [
        ("2946", "2946"),
        ("1 234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("87%", "87"),
        ("(1 200)", "-1200"),
        ("-15,5", "-15.5"),
        ("12,5 тыс. руб.", "12.5"),
        (42, "42"),
        (3.5, "3.5"),
    ]
    for raw, want in cases:
        d = numeric.to_decimal(raw)
        check(f"разбор «{raw}»",
              d is not None and numeric.exact(d) == want,
              f"получено {d!r}, ожидалось {want}")
    for raw in ("не число", "", None, "2026-03", "IV квартал"):
        check(f"«{raw}» — не число",
              numeric.to_decimal(raw) is None, repr(numeric.to_decimal(raw)))


def test_format_sniff():
    from openpyxl import Workbook
    tmp = Path(tempfile.mkdtemp(prefix="dash_fmt_"))
    xlsx = tmp / "t.xlsx"
    wb = Workbook()
    wb.active.append(["a", 1])
    wb.save(xlsx)
    xlsx_bytes = xlsx.read_bytes()

    text_bytes = "просто текст".encode("utf-8")
    ole_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64

    def code(path, head, declared):
        try:
            return intake.check_format(path, head, declared)
        except HTTPException as e:
            return e.status_code

    check("xlsx принимается как xlsx",
          code(xlsx, xlsx_bytes[:4096], ".xlsx") == ".xlsx")
    check("xlsx, названный pdf -> 415",
          code(xlsx, xlsx_bytes[:4096], ".pdf") == 415)
    check("текст принимается как txt",
          code(tmp / "t.txt", text_bytes[:4096], ".txt") == ".txt")
    check("текст принимается как csv",
          code(tmp / "t.csv", text_bytes[:4096], ".csv") == ".csv")
    check("pdf из текста -> 415",
          code(tmp / "t.pdf", text_bytes[:4096], ".pdf") == 415)
    check(".doc не поддерживается с инструкцией -> 400",
          code(tmp / "t.doc", ole_bytes[:4096], ".doc") == 400)
    check("старый docx (OLE) -> 415",
          code(tmp / "t.docx", ole_bytes[:4096], ".docx") == 415)
    check("неизвестный расширение -> 400",
          code(tmp / "t.exe", text_bytes[:4096], ".exe") == 400)


def test_raw_xlsx_coordinates():
    """Сырьё хранит лист, строку, колонку и адрес ячейки (ТЗ §5.1)."""
    from openpyxl import Workbook
    tmp = Path(tempfile.mkdtemp(prefix="dash_raw_"))
    p = tmp / "t.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Показатели"
    ws.append(["Показатель", "Значение"])
    ws.append(["Контингент", 2946])
    wb.save(p)

    rows = intake.build_raw(p, ".xlsx", "", [])
    cells = [r for r in rows if r.get("kind") == "cell"]
    check("ячейка с числом найдена", len(cells) == 1, str(rows))
    if cells:
        c = cells[0]
        check("адрес ячейки сохранён", c.get("cell_ref") == "B2",
              str(c.get("cell_ref")))
        check("лист сохранён", c.get("sheet") == "Показатели",
              str(c.get("sheet")))
        check("строка сохранена", c.get("row_no") == 2, str(c.get("row_no")))
        check("колонка сохранена", c.get("col_no") == 2, str(c.get("col_no")))
        check("значение как текст", c.get("text") == "2946", str(c.get("text")))
    check("строка с подписью тоже в сырье",
          any(r.get("kind") == "row" and "Контингент" in (r.get("text") or "")
              for r in rows), str(rows)[:300])


def test_sum_check():
    """Сумма составляющих против строки «Итого» (ТЗ §10)."""
    conn, _ = _fresh_db()
    cur = conn.execute(
        "INSERT INTO upload(division_id, filename, rel_path, sha256,"
        " size_bytes, uploaded_at, status) VALUES(?,?,?,?,?,?,?)",
        ("buhgalteria", "s.xlsx", "buhgalteria/s.xlsx", "abc", 1,
         "2026-03-01 10:00:00", "needs_review"))
    uid = int(cur.lastrowid)

    def add(row_no, label, text):
        conn.execute(
            "INSERT INTO upload_raw(upload_id, kind, label, text, sheet,"
            " row_no, seq) VALUES(?,?,?,?,?,?,?)",
            (uid, "row", f"S!{row_no}", text, "S", row_no, row_no))

    add(1, "строка 1", "Позиция А | 100")
    add(2, "строка 2", "Позиция Б | 200")
    add(3, "строка 3", "Итого | 999")
    conn.commit()

    findings = checks.run(conn, uid)
    codes = [f["check_code"] for f in findings]
    check("рассогласование суммы найдено", "sum_mismatch" in codes, str(codes))
    bad = [f for f in findings if f["check_code"] == "sum_mismatch"]
    if bad:
        check("сумма посчитана как 300", bad[0]["value_a"] == "300",
              str(bad[0]))
        check("итог в ошибке — 999", bad[0]["value_b"] == "999",
              str(bad[0]))
        check("сумма — блокирующая ошибка", bad[0]["severity"] == "error",
              str(bad[0]["severity"]))

    # Совпадает → проверка замолкает.
    conn.execute("UPDATE upload_raw SET text = 'Итого | 300'"
                 " WHERE upload_id = ? AND row_no = 3", (uid,))
    conn.commit()
    codes2 = [f["check_code"] for f in checks.run(conn, uid)]
    check("совпадающая сумма не даёт ошибки", "sum_mismatch" not in codes2,
          str(codes2))


def test_period_candidate_no_mutation():
    """Проверка периода не создаёт записи в справочнике."""
    conn, _ = _fresh_db()
    before = conn.execute("SELECT COUNT(*) FROM dim_period").fetchone()[0]
    pid, exists = checks.period_candidate(conn, "2026-03")
    after = conn.execute("SELECT COUNT(*) FROM dim_period").fetchone()[0]
    check("существующий период найден", pid == "2026-03" and exists,
          f"{pid},{exists}")
    check("период не создан проверкой", before == after,
          f"{before} -> {after}")
    pid2, exists2 = checks.period_candidate(conn, "V квартал 2099")
    check("неизвестный период: id есть, но записи нет",
          pid2 is not None and not exists2, f"{pid2},{exists2}")
    check("и он не создан", conn.execute(
        "SELECT COUNT(*) FROM dim_period").fetchone()[0] == after, "")


def test_journal_csv_escapes_formulas():
    items = [{"id": 1, "ts": "2026-03-01", "actor": "admin", "role": "admin",
              "action": "export", "entity": "xlsx",
              "detail": "=1+1"}, {"id": 2, "ts": "2026-03-02", "actor": "a",
                                   "role": "b", "action": "login",
                                   "entity": None, "detail": "-сумма"}]
    data = journal.to_csv(items)
    check("формула экранирована", "':=1+1" not in data and "'=1+1" in data,
          data)
    check("вторая ячейка экранирована", "'-сумма" in data, data)
    check("заголовок на месте", data.splitlines()[0].startswith("id,ts,actor"),
          data.splitlines()[0])


def test_login_limit_and_hash_upgrade():
    conn, _ = _fresh_db()
    # Апгрейд хэша: сид пишет PBKDF2, после входа должен стать Argon2.
    ok = auth.verify_login("admin", "admin")
    row = conn.execute("SELECT pass_hash FROM division WHERE division_id='admin'"
                       ).fetchone()
    if auth.ARGON2:
        check("после входа пароль хранится в Argon2",
              row["pass_hash"].startswith("$argon2"), row["pass_hash"][:20])
    else:
        check("argon2 не установлен — хэш остался прежним",
              not row["pass_hash"].startswith("$argon2"), row["pass_hash"][:20])
    check("повторный вход после апгрейда работает",
          auth.verify_login("admin", "admin") == "admin", str(ok))

    # Лимит попыток.
    auth.clear_attempts("victim")
    check("до лимита блокировки нет", auth.login_blocked("victim") is None, "")
    for _ in range(config.LOGIN_MAX_ATTEMPTS):
        auth.record_login("victim", "127.0.0.1", False)
    reason = auth.login_blocked("victim")
    check(f"после {config.LOGIN_MAX_ATTEMPTS} неудач вход блокирован",
          reason is not None and "заблокирован" in reason, str(reason))
    check("другой логин не заблокирован", auth.login_blocked("other") is None,
          "")
    auth.clear_attempts("victim")
    check("успех снимает блокировку", auth.login_blocked("victim") is None, "")

    # Выключенный владельцем лимит не мешает.
    old = config.LOGIN_LIMIT_ON
    config.LOGIN_LIMIT_ON = False
    check("RECTOR_LOGIN_LIMIT=0 отключает лимит",
          auth.login_blocked("victim") is None, "")
    config.LOGIN_LIMIT_ON = old


def test_model_availability_is_exact():
    """Наличие qwen2.5:1.5b не означает наличия qwen2.5:14b (ТЗ §13.8)."""
    have = ["qwen2.5:1.5b", "bge-m3:latest"]
    check("свой тег найден", llm.model_available("qwen2.5:1.5b", have), "")
    check("чужой тег НЕ считается установленным",
          not llm.model_available("qwen2.5:14b", have), "")
    check("имя без тега матчится по базовому имени",
          llm.model_available("qwen2.5", have), "")
    check("без списка моделей — не установлен",
          not llm.model_available("qwen2.5:14b", []), "")


def test_apply_card_validates_client():
    """Клиенту нельзя подсунуть несуществующий показатель или ссылку."""
    conn, _ = _fresh_db()
    cur = conn.execute(
        "INSERT INTO upload(division_id, filename, rel_path, sha256,"
        " size_bytes, uploaded_at, status) VALUES(?,?,?,?,?,?,?)",
        ("buhgalteria", "a.xlsx", "buhgalteria/a.xlsx", "zzz", 1,
         "2026-03-01 10:00:00", "needs_review"))
    uid = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO parse_card(upload_id, doc_type, period, mapping_json,"
        " values_json, unmapped_json, status) VALUES(?,?,?,?,?,?,?)",
        (uid, "справка", "2026-03", "[]", "[]", "[]", "draft"))
    conn.commit()

    card_in = {"mapping": [
        {"metricId": "нет-такого-показателя", "value": "10"},
        {"metricId": "applicant-2025", "value": "412", "rawRef": 9999},
    ], "unmapped": [{"label": "x" * 500, "value": "1"}],
        "docType": "T" * 500, "period": "2026-03"}
    card = review.apply_card(conn, uid, card_in, "admin")
    check("неизвестный показатель отброшен",
          card["mapping"][0]["metricId"] == "", str(card["mapping"][0]))
    check("ссылка на несуществующую строку сырья отброшена",
          card["mapping"][1]["rawRef"] is None, str(card["mapping"][1]))
    check("значение сохранено", card["mapping"][1]["value"] == "412",
          str(card["mapping"][1]))
    check("длинные строки обрезаны",
          len(card["docType"]) <= 120
          and len(card["unmapped"][0]["label"]) <= 200,
          f"{len(card['docType'])}, {len(card['unmapped'][0]['label'])}")

    # Что нашла модель, должно пережить правку человека: клиент поле
    # modelValue не шлёт, и раньше оно пропадало после «Сохранить
    # правки» — проверяющий терял расхождение «модель: 3358,
    # внесено: 2946».
    conn.execute(
        "UPDATE parse_card SET mapping_json = ? WHERE upload_id = ?",
        (json.dumps([{"metricId": "applicant-2025", "value": "412",
                      "modelValue": "3358", "source": "model"}],
                    ensure_ascii=False), uid))
    conn.commit()
    card = review.apply_card(conn, uid, {"mapping": [
        {"metricId": "applicant-2025", "value": "400"}]}, "admin")
    check("подпись модели пережила правку человека",
          card["mapping"][0].get("modelValue") == "3358",
          str(card["mapping"][0]))


def test_llm_chat_accepts_role_and_traces():
    """`llm.chat` принимает роль small/large и пишет трассу (ТЗ §13).

    Дубликат функции в llm.py (старая версия без role перекрывала новую)
    тестами не ловился: сигнатура была, а живой конвейер падал на
    `chat() got an unexpected keyword argument 'role'`. Проверяем и то,
    и другое — плюс саму запись в llm_trace.
    """
    import inspect
    params = inspect.signature(llm.chat).parameters
    check("llm.chat принимает role", "role" in params, list(params))
    check("llm.chat принимает purpose", "purpose" in params, list(params))

    src = Path(llm.__file__).read_text(encoding="utf-8")
    defs = [l for l in src.splitlines() if l.startswith("def chat(")]
    check("chat определён ровно один раз (второе перекрывает первое)",
          len(defs) == 1, len(defs))

    conn, _ = _fresh_db()
    text, meta = llm.chat("система", "вопрос",
                          model="нет-такой-модели-xyz", role="small",
                          purpose="unit")
    check("несуществующая модель даёт честный отказ, а не подмену",
          text == "" and meta.get("ok") is False, str(meta))
    n = conn.execute("SELECT COUNT(*) FROM llm_trace").fetchone()[0]
    check("вызов записан в llm_trace", n == 1, n)
    _teardown()


def test_extract_json_tolerates_repeats():
    """Большие модели повторяют ответ — разбор не должен от этого падать.

    Замер Vikhr-7B на ноутбуке: на запрос parse card модель вернула
    «{...} {...} {...} {...} {...}». Старый разбор брал от первой до
    последней скобки и получал слипшийся поток, то есть карточка
    падала бы в фолбэк на живом показе.
    """
    one = '{"mapping": [{"metricId": "continuer-total", "value": "2946"}]}'
    check("один объект разбирается",
          intake._extract_json(one)["mapping"][0]["value"] == "2946", "")
    check("повтор ответа не ломает разбор",
          intake._extract_json(one + " " + one + " " + one)["mapping"][0]
          ["value"] == "2946", "")
    check("пояснение перед JSON не мешает",
          intake._extract_json("JSON-объект: " + one)["mapping"][0]["value"]
          == "2946", "")
    check("блок кода снимается",
          intake._extract_json("Вот ответ:\n```json\n" + one + "\n```")
          ["mapping"], "")
    braces = '{"note": "в фигурных скобках {внутри строки}", "v": 1}'
    check("скобки внутри строк не путают баланс",
          intake._extract_json(braces)["v"] == 1, str(intake._extract_json(braces)))
    trunc_values = (
        '{"doc_type": "таблица", "period": "2026-03", "values": ['
        '{"label": "Контингент", "value": "2946", "metric_id": "continuer-total", "raw_ref": 1}, '
        '{"label": "Приём", "value": "412'
    )
    salv = intake._extract_json(trunc_values)
    check("оборванный массив values сохраняет готовые элементы",
          len(salv.get("values") or []) == 1 and salv["values"][0]["value"] == "2946",
          str(salv))
    try:
        intake._extract_json('{"mapping": [')
        check("обрезанный JSON даёт понятную ошибку", False, "")
    except ValueError as exc:
        check("обрезанный JSON даёт понятную ошибку",
              "не закрыт" in str(exc), str(exc))
    try:
        intake._extract_json("без единой скобки")
        check("ответа без JSON отклоняется", False, "")
    except ValueError as exc:
        check("ответа без JSON отклоняется", "нет JSON" in str(exc), str(exc))


def test_reindex_preserves_doc_meta_and_upload_link():
    """Переиндексация (force=True) не должна стирать doc_meta и upload.doc_id.

    При PRAGMA foreign_keys = ON удаление строки из doc запускает
    ON DELETE CASCADE по doc_meta и metric_source и ON DELETE SET NULL
    по upload.doc_id и fact_provenance.source_doc_id. Обновление существующего
    документа обязано сохранять его doc_id.
    """
    conn, _ = _fresh_db()
    try:
        config.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        fpath = config.KNOWLEDGE_DIR / "report_2026.txt"
        fpath.write_text("Контингент обучающихся составляет 2946 человек.",
                         encoding="utf-8")
        res1 = ingest_mod.index_file(conn, fpath, is_demo=False, force=True)
        check("первичная индексация создала чанки",
              res1["status"] == "indexed" and res1["chunks"] >= 1, str(res1))
        doc_row = conn.execute(
            "SELECT doc_id FROM doc WHERE rel_path = 'report_2026.txt'"
        ).fetchone()
        doc_id = int(doc_row["doc_id"])

        cur = conn.execute(
            "INSERT INTO upload(division_id, doc_id, filename, rel_path, sha256,"
            " size_bytes, uploaded_at, status) VALUES(?,?,?,?,?,?,?,?)",
            ("org_control", doc_id, "report_2026.txt", "report_2026.txt",
             "abc", 48, "2026-10-29 10:00:00", "needs_review"))
        up_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO doc_meta(doc_id, upload_id, division_id, title, purpose,"
            " period, frequency, responsible, access_level, metric_ids,"
            " confirmed, uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, up_id, "org_control", "Отчёт 2026", "Контроль",
             "2026-03", "ежемесячно", "Иванов И.И.", "internal",
             '["continuer-total"]', 1, "2026-10-29 10:00:00"))
        conn.execute(
            "INSERT INTO metric_source(metric_id, doc_id, confirmed)"
            " VALUES(?,?,1)", ("continuer-total", doc_id))
        conn.commit()

        # Повторная переиндексация с force=True
        res2 = ingest_mod.index_file(conn, fpath, is_demo=False, force=True)
        check("повторная индексация прошла",
              res2["status"] == "indexed", str(res2))
        doc_row2 = conn.execute(
            "SELECT doc_id FROM doc WHERE rel_path = 'report_2026.txt'"
        ).fetchone()
        check("doc_id сохранён при переиндексации",
              int(doc_row2["doc_id"]) == doc_id,
              f"{doc_row2['doc_id']} != {doc_id}")
        meta_n = conn.execute(
            "SELECT COUNT(*) FROM doc_meta WHERE doc_id = ?", (doc_id,)
        ).fetchone()[0]
        check("анкета doc_meta не удалена каскадом", meta_n == 1, meta_n)
        up_doc = conn.execute(
            "SELECT doc_id FROM upload WHERE upload_id = ?", (up_id,)
        ).fetchone()["doc_id"]
        check("связь upload.doc_id не занулилась", up_doc == doc_id, up_doc)
        ms_n = conn.execute(
            "SELECT COUNT(*) FROM metric_source WHERE doc_id = ?", (doc_id,)
        ).fetchone()[0]
        check("связь metric_source не удалена каскадом", ms_n == 1, ms_n)
    finally:
        conn.close()
        _teardown()


def test_supplement_from_rows_recovers_tail_metrics():
    """Если модель 1.5B вернула первые 8 строк таблицы и пропустила хвост,
    _supplement_from_rows добирает точные совпадения из upload_raw (kind='row').
    """
    conn, _ = _fresh_db()
    try:
        cat = intake._catalog(conn)
        raw_rows = [
            {"raw_id": 1, "kind": "row",
             "text": "Контингент обучающихся | 2946 | чел.", "seq": 0},
            {"raw_id": 17, "kind": "row",
             "text": "Действующие образовательные программы | 38 | шт.", "seq": 1},
            {"raw_id": 19, "kind": "row",
             "text": "Трудоустройство выпускников | 91.2 | %", "seq": 2},
        ]
        partial = [{"label": "Контингент обучающихся", "value": "2946",
                    "metricId": "continuer-total", "rawRef": 1}]
        full = intake._supplement_from_rows(conn, cat, raw_rows, partial)
        by_mid = {v["metricId"]: v for v in full}
        check("добраны программы (38, rawRef=17)",
              by_mid.get("programs", {}).get("value") == "38"
              and by_mid.get("programs", {}).get("rawRef") == 17,
              str(full))
        check("добрано трудоустройство (91.2, rawRef=19)",
              by_mid.get("trudoustroystvo", {}).get("value") == "91.2"
              and by_mid.get("trudoustroystvo", {}).get("rawRef") == 19,
              str(full))
        check("пустой ответ модели не подменяется",
              intake._supplement_from_rows(conn, cat, raw_rows, []) == [],
              "должен остаться пустым")
    finally:
        conn.close()
        _teardown()


def main():
    print("Проверки детерминированной части конвейера")
    try:
        test_numeric()
        test_format_sniff()
        test_raw_xlsx_coordinates()
        test_sum_check()
        test_period_candidate_no_mutation()
        test_journal_csv_escapes_formulas()
        test_login_limit_and_hash_upgrade()
        test_model_availability_is_exact()
        test_apply_card_validates_client()
        test_llm_chat_accepts_role_and_traces()
        test_extract_json_tolerates_repeats()
        test_reindex_preserves_doc_meta_and_upload_link()
        test_supplement_from_rows_recovers_tail_metrics()
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
