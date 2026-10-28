# -*- coding: utf-8 -*-
"""
Экран проверки: состояние загрузки и решение человека (ТЗ §10).

Здесь заканчивается машина и начинается человек: карточка, проверки,
конфликты, объяснение и три решения — «принять», «исправить»,
«отклонить». Публикация выполняется в одной транзакции вместе с
провенансом: если провенанс не записался, значение не публикуется
(ТЗ §5.5).

Идемпотентность: повторное «принять» той же загрузкой приводит к тому же
состоянию — fact_metric и fact_provenance обновляются, дублей не
появляется (UNIQUE по периоду/показателю/организации/загрузке).
"""
import json
import time

from fastapi import HTTPException

from . import checks
from . import config
from . import consistency
from . import db
from . import journal
from . import numeric
from .uploads import _ensure_period

RAW_PAGE = 300        # строк сырья в выдаче экрана проверки
PUBLISHER_ORG = "ALL"  # значения публикуются по университету в целом


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_upload(conn, upload_id: int, viewer: str | None) -> dict:
    row = conn.execute(
        """SELECT u.*, d.title AS division_title
             FROM upload u JOIN division d ON d.division_id = u.division_id
            WHERE u.upload_id = ?""", (upload_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Загрузка не найдена")
    if viewer and viewer != "admin" and row["division_id"] != viewer:
        # Без деталей чужой загрузки: 403 не раскрывает ни имени файла,
        # ни того, что она вообще существует для другого подразделения.
        raise HTTPException(403, detail="Это загрузка другого подразделения")
    return dict(row)


def _catalog(conn) -> dict:
    cat = {}
    for r in conn.execute(
            "SELECT metric_id, title, unit, target, dir, source FROM dim_metric"
            " ORDER BY ord"):
        cat[r["metric_id"]] = dict(r)
    return cat


def _valid_refs(conn, upload_id: int) -> set[int]:
    return {r["raw_id"] for r in conn.execute(
        "SELECT raw_id FROM upload_raw WHERE upload_id = ?", (upload_id,))}


def _sanitize_unmapped(items) -> list[dict]:
    out = []
    for u in (items or [])[:100]:
        if not isinstance(u, dict):
            continue
        out.append({
            "label": str(u.get("label") or "")[:200],
            "value": str(u.get("value") or "")[:64],
            "rawRef": u.get("rawRef"),
            "reason": str(u.get("reason") or "")[:200]})
    return out


def apply_card(conn, upload_id: int, card_in: dict | None,
               editor: str | None) -> dict:
    """Принимает исправленную карточку от клиента и сохраняет её.

    Сервер не верит клиенту: metric_id проверяется по каталогу, число —
    по числовому разбору, raw_ref — по существующим строкам сырья.
    """
    card = checks.load_card(conn, upload_id) or {
        "docType": None, "period": None, "periodId": None,
        "mapping": [], "values": [], "unmapped": []}
    if not card_in:
        return card

    cat = _catalog(conn)
    refs = _valid_refs(conn, upload_id)
    # Что модель нашла в файле, показывается на экране проверки рядом
    # с правкой человека («модель: 3358», внесено «2946»). Клиент это
    # поле не присылает — оно его не редактирует, — но выбрасывать его
    # после «Сохранить правки» нельзя: проверяющий лишается
    # расхождения, ради которого карточку вообще проверяют.
    seen_by_model = {
        str(m.get("metricId") or ""): m.get("modelValue")
        for m in (card.get("mapping") or [])
        if m.get("modelValue") is not None}
    mapping = []
    for m in (card_in.get("mapping") or [])[:200]:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("metricId") or "").strip()
        known = mid in cat
        ref = m.get("rawRef")
        try:
            ref = int(ref) if ref is not None else None
        except (TypeError, ValueError):
            ref = None
        if ref not in refs:
            ref = None
        val = m.get("value")
        val = "" if val is None else str(val)[:64]
        title = cat.get(mid, {}).get("title") if known else None
        seen = seen_by_model.get(mid if known else mid)
        row = {
            "metricId": mid if known else "",
            "metricTitle": title,
            "unit": cat.get(mid, {}).get("unit") if known else None,
            "value": val,
            "source": str(m.get("source") or "manual")[:40],
            "confidence": m.get("confidence"),
            "rawRef": ref,
            "label": str(m.get("label") or title or "без показателя")[:200],
        }
        if seen is not None:
            row["modelValue"] = seen
        mapping.append(row)

    card["mapping"] = mapping
    card["values"] = [m for m in mapping if str(m.get("value") or "").strip()]
    card["unmapped"] = _sanitize_unmapped(card_in.get("unmapped"))
    card["docType"] = str(card_in.get("docType") or card.get("docType")
                          or "")[:120] or None
    card["period"] = str(card_in.get("period") or card.get("period")
                         or "")[:80] or None
    pid = card_in.get("periodId")
    if pid:
        card["periodId"] = str(pid)[:40]
    checks.save_card(conn, upload_id, card, status="corrected",
                     edited_by=editor)
    return card


def _conflicts(conn, upload_id: int, card: dict,
               pid: str | None) -> tuple[list[dict], list[dict]]:
    """(конфликты с чужой загрузкой, заменяемые значения без провенанса)."""
    conflicts, replaced = [], []
    if not pid:
        return conflicts, replaced
    for m in card.get("mapping") or []:
        mid = m.get("metricId")
        if not mid or numeric.to_decimal(m.get("value")) is None:
            continue
        ex = checks._existing_value(conn, pid, mid)
        if not ex:
            continue
        item = {"metricId": mid,
                "title": m.get("metricTitle") or mid,
                "periodId": pid,
                "existingValue": ex.get("value_exact") or ex.get("value"),
                "proposed": m.get("value"),
                "fromUpload": ex.get("src_upload"),
                "fromFile": ex.get("src_file")}
        if ex.get("src_upload") and int(ex["src_upload"]) != upload_id:
            conflicts.append(item)
        else:
            item["reason"] = ("демонстрационное значение" if ex.get("src_upload")
                              is None else "значение этой же загрузки")
            replaced.append(item)
    return conflicts, replaced


def payload(conn, upload_id: int, viewer: str | None) -> dict:
    up = load_upload(conn, upload_id, viewer)

    meta = conn.execute(
        """SELECT title, purpose, period, frequency, responsible,
                  access_level, metric_ids, confirmed, next_expected
             FROM doc_meta WHERE doc_id = ?""", (up["doc_id"],)).fetchone()
    meta = dict(meta) if meta else {}

    card = checks.load_card(conn, upload_id) or {
        "docType": None, "period": meta.get("period"), "periodId": None,
        "mapping": [], "values": [], "unmapped": [], "parseError": None}

    findings = [dict(r) for r in conn.execute(
        "SELECT check_code, severity, message, field, value_a, value_b"
        " FROM upload_check WHERE upload_id = ? ORDER BY id",
        (upload_id,)).fetchall()]
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    pid = card.get("periodId")
    if not pid:
        pid, _exists = checks.period_candidate(
            conn, card.get("period") or meta.get("period"))
    conflicts, replaced = _conflicts(conn, upload_id, card, pid)

    raw = [dict(r) for r in conn.execute(
        """SELECT raw_id, kind, label, text, page_no, sheet, row_no, col_no,
                  cell_ref
             FROM upload_raw WHERE upload_id = ?
            ORDER BY seq LIMIT ?""", (upload_id, RAW_PAGE)).fetchall()]
    raw_total = conn.execute(
        "SELECT COUNT(*) AS n FROM upload_raw WHERE upload_id = ?",
        (upload_id,)).fetchone()["n"]

    cat = _catalog(conn)
    periods = [dict(r) for r in conn.execute(
        "SELECT period_id, label FROM dim_period ORDER BY ord")]

    decision = [dict(r) for r in conn.execute(
        "SELECT actor, action, comment, decided_at, published"
        " FROM upload_decision WHERE upload_id = ? ORDER BY id DESC",
        (upload_id,)).fetchall()]

    expl = consistency.cached(conn, upload_id)
    expl_status = llm_status()

    return {
        "upload": {
            "id": up["upload_id"],
            "divisionId": up["division_id"],
            "divisionTitle": up["division_title"],
            "filename": up.get("original_name") or up["filename"],
            "serverName": up["filename"],
            "extension": up.get("extension"),
            "mime": up.get("mime_detected"),
            "sha256": up["sha256"],
            "sizeBytes": up["size_bytes"],
            "uploadedAt": up["uploaded_at"],
            "uploadedBy": up.get("uploaded_by"),
            "status": up["status"],
            "docId": up["doc_id"],
            "error": up.get("error_message") or up.get("error"),
        },
        "questionnaire": {
            "title": meta.get("title"), "purpose": meta.get("purpose"),
            "period": meta.get("period"), "frequency": meta.get("frequency"),
            "responsible": meta.get("responsible"),
            "accessLevel": meta.get("access_level"),
            "metricIds": _json(meta.get("metric_ids"), []),
            "confirmed": bool(meta.get("confirmed")),
            "nextExpected": meta.get("next_expected"),
        },
        "card": {
            "docType": card.get("docType"),
            "period": card.get("period"),
            "periodId": card.get("periodId"),
            "status": card.get("status"),
            "model": card.get("model"),
            "attempts": card.get("attempts"),
            "parseMs": card.get("parseMs"),
            "parseError": card.get("parseError"),
            "editedAt": card.get("editedAt"),
            "editedBy": card.get("editedBy"),
            "explanation": card.get("explanation"),
            "mapping": card.get("mapping") or [],
            "values": card.get("values") or [],
            "unmapped": card.get("unmapped") or [],
        },
        "checks": findings,
        "summary": {"errors": len(errors), "warnings": len(warnings),
                    "canAccept": not errors,
                    "conflicts": len(conflicts)},
        "conflicts": conflicts,
        "replaced": replaced,
        "explanation": expl,
        "explanationStatus": expl_status,
        "raw": raw,
        "rawTotal": raw_total,
        "metrics": [{"id": m, "title": v["title"], "unit": v["unit"],
                     "target": v["target"], "dir": v["dir"],
                     "source": v["source"]}
                    for m, v in cat.items()],
        "periods": periods,
        "decisions": decision,
    }


def llm_status() -> dict:
    try:
        from . import llm
        st = llm.role_status("large")
        return {"available": bool(st.get("installed")),
                "model": st.get("model"), "fallback": bool(st.get("fallback")),
                "note": st.get("note")}
    except Exception:
        return {"available": False, "model": None}


def _json(s, default):
    try:
        v = json.loads(s or "")
        return v
    except (json.JSONDecodeError, TypeError):
        return default


def _method(ext: str | None, raw_kind: str | None, sheet: str | None) -> str:
    """Каким способом получено значение — для провенанса (ТЗ §5.5)."""
    ext = (ext or "").lstrip(".").lower()
    if raw_kind == "cell":
        return f"{ext or 'xlsx'}_cell"
    if raw_kind == "page":
        return "pdf_page"
    if raw_kind == "row":
        return f"{ext or 'xlsx'}_row"
    if sheet and str(sheet).startswith("таблица"):
        return "docx_table"
    if raw_kind == "block":
        return "docx_block" if ext == "docx" else "text_block"
    return "text_block"


def publish(conn, upload_id: int, up: dict, card: dict, pid: str,
            actor: str, comment: str | None) -> dict:
    """Транзакция «значение + провенанс». Источник обязателен."""
    refs = _valid_refs(conn, upload_id)
    raw_by_id = {}
    if refs:
        for r in conn.execute(
                "SELECT raw_id, kind, label, text, cell_ref, sheet, row_no"
                " FROM upload_raw WHERE upload_id = ?", (upload_id,)):
            raw_by_id[r["raw_id"]] = dict(r)

    now = _now()
    title = up.get("_title") or ""
    parser_model = card.get("model")
    published = 0
    conn.commit()          # закрыть возможный незакрытый транзакционный хвост
    conn.execute("BEGIN")
    try:
        for m in card.get("mapping") or []:
            mid = m.get("metricId")
            if not mid:
                continue
            d = numeric.to_decimal(m.get("value"))
            if d is None:
                continue
            f = numeric.to_float(d)
            if f is None:
                raise ValueError("значение вне допустимого диапазона")
            # 1. Витрина: REAL для графиков, точная строка — источник истины.
            conn.execute(
                "INSERT INTO fact_metric(period_id, metric_id, org_id, value,"
                " status, value_exact) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(period_id, metric_id, org_id)"
                " DO UPDATE SET value = excluded.value,"
                " value_exact = excluded.value_exact, status = excluded.status",
                (pid, mid, PUBLISHER_ORG, f, "none", numeric.exact(d)))
            # 2. Провенанс: без него значение не считается опубликованным.
            ref = m.get("rawRef") if m.get("rawRef") in raw_by_id else None
            raw = raw_by_id.get(ref) if ref else None
            if raw:
                method = _method(up.get("extension"), raw.get("kind"),
                                 raw.get("sheet"))
            elif m.get("source") == "questionnaire":
                method = "questionnaire"   # число ввели на форме анкеты
            else:
                method = "model_extract"   # модель нашла в тексте, без адреса
            conn.execute(
                "INSERT INTO fact_provenance(period_id, metric_id, org_id,"
                " source_upload_id, source_doc_id, source_file_name, sha256,"
                " extracted_value, value_exact, extraction_method,"
                " parser_version, parser_model, confirmed_by, confirmed_at,"
                " note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(period_id, metric_id, org_id, source_upload_id)"
                " DO UPDATE SET source_doc_id = excluded.source_doc_id,"
                " source_file_name = excluded.source_file_name,"
                " sha256 = excluded.sha256,"
                " extracted_value = excluded.extracted_value,"
                " value_exact = excluded.value_exact,"
                " extraction_method = excluded.extraction_method,"
                " parser_model = excluded.parser_model,"
                " confirmed_by = excluded.confirmed_by,"
                " confirmed_at = excluded.confirmed_at, note = excluded.note",
                (pid, mid, PUBLISHER_ORG, upload_id, up.get("doc_id"),
                 up.get("original_name") or up.get("filename"),
                 up.get("sha256"),
                 (raw.get("text") if raw else None) or str(m.get("value")),
                 numeric.exact(d),
                 method,
                 "2.0", parser_model, actor, now, comment))
            # 3. Связь «документ кормит показатель» + снятие пометки демо.
            conn.execute(
                "INSERT OR REPLACE INTO metric_source(metric_id, doc_id,"
                " confirmed) VALUES(?,?,1)", (mid, up.get("doc_id")))
            conn.execute(
                "UPDATE dim_metric SET source = 'official', src_note = ?"
                " WHERE metric_id = ?", (title or up.get("original_name"), mid))
            published += 1

        conn.execute(
            "UPDATE doc_meta SET confirmed = 1 WHERE doc_id = ?",
            (up.get("doc_id"),))
        conn.execute(
            "UPDATE upload SET status = 'accepted', decided_at = ?"
            " WHERE upload_id = ?", (now, upload_id))
        conn.execute(
            "INSERT INTO upload_decision(upload_id, actor, action, comment,"
            " decided_at, published) VALUES(?,?,?,?,?,?)",
            (upload_id, actor, "accept", comment, now, published))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"published": published, "periodId": pid}


def decide(conn, upload_id: int, actor: str, viewer: str | None,
           body: dict) -> dict:
    up = load_upload(conn, upload_id, viewer)
    action = str(body.get("action") or "").strip()
    comment = str(body.get("comment") or "")[:500] or None
    card_in = body.get("card")

    if action == "fix":
        apply_card(conn, upload_id, card_in, actor)
        findings = checks.run(conn, upload_id)
        journal.log(conn, "decide_accept", actor=actor, role=viewer,
                    entity=f"upload:{upload_id}", detail="исправление карточки")
        errors = sum(1 for f in findings if f["severity"] == "error")
        return {"status": "needs_review", "saved": True, "errors": errors,
                "warnings": len(findings) - errors}

    if action == "reject":
        now = _now()
        conn.execute(
            "UPDATE upload SET status = 'rejected', decided_at = ?"
            " WHERE upload_id = ?", (now, upload_id))
        conn.execute(
            "INSERT INTO upload_decision(upload_id, actor, action, comment,"
            " decided_at, published) VALUES(?,?,?,?,?,0)",
            (upload_id, actor, "reject", comment, now))
        conn.commit()
        journal.log(conn, "decide_reject", actor=actor, role=viewer,
                    entity=f"upload:{upload_id}", detail=comment)
        return {"status": "rejected", "published": 0}

    if action != "accept":
        raise HTTPException(400,
                            detail="Неизвестное действие: ожидается "
                                   "accept, fix или reject")

    # --- карточка от клиента -------------------------------------------
    card = apply_card(conn, upload_id, card_in, actor)

    # --- проверки заново: исправления могли убрать и создать ошибки ----
    findings = checks.run(conn, upload_id)
    errors = [f for f in findings if f["severity"] == "error"]

    overwrite = body.get("overwrite")
    overwrite_all = overwrite is True
    overwrite_set = set(overwrite) if isinstance(overwrite, list) else set()

    def blocked(f):
        if f["check_code"] != "duplicate_value":
            return True
        return not (overwrite_all or f.get("field") in overwrite_set)

    blocking = [f for f in errors if blocked(f)]
    if blocking:
        raise HTTPException(409, detail={
            "code": "blocking",
            "message": "Обнаружены блокирующие ошибки — публикация запрещена",
            "checks": blocking})

    # --- период ----------------------------------------------------------
    meta = conn.execute("SELECT period FROM doc_meta WHERE doc_id = ?",
                        (up.get("doc_id"),)).fetchone()
    label = card.get("period") or (meta["period"] if meta else None)
    pid, exists = (None, False)
    if label:
        pid, exists = checks.period_candidate(conn, label)
        if not pid:
            # Период записан в свободной форме («I квартал 2026»):
            # заводим его с ярлыком, как это делал приём файла.
            pid = _ensure_period(conn, str(label))
            exists = True
        elif not exists:
            _ensure_period(conn, str(label))
    if not pid:
        pid = card.get("periodId")
        exists = bool(pid and conn.execute(
            "SELECT 1 FROM dim_period WHERE period_id = ?", (pid,)).fetchone())
    if not pid:
        raise HTTPException(409, detail={
            "code": "no_period",
            "message": "Не удалось определить период публикации — "
                       "укажите период в карточке"})
    if not exists:
        _ensure_period(conn, label or str(pid))   # создаёт запись и коммитит

    # --- конфликты --------------------------------------------------------
    conflicts, _replaced = _conflicts(conn, upload_id, card, pid)
    if conflicts:
        uncovered = [c for c in conflicts
                     if not (overwrite_all or c["metricId"] in overwrite_set)]
        if uncovered:
            raise HTTPException(409, detail={
                "code": "conflict",
                "message": "Значение уже подтверждено другой загрузкой — "
                           "нужно явно подтвердить перезапись",
                "conflicts": uncovered})

    up["_title"] = (conn.execute(
        "SELECT title FROM doc_meta WHERE doc_id = ?",
        (up.get("doc_id"),)).fetchone() or {"title": ""})["title"]

    try:
        res = publish(conn, upload_id, up, card, pid, actor, comment)
    except HTTPException:
        raise
    except Exception as exc:
        # Транзакция откатилась целиком: ни значения, ни провенанса.
        raise HTTPException(
            500, detail=f"Публикация не выполнена, изменения отменены: "
                        f"{type(exc).__name__}") from exc

    journal.log(conn, "publish", actor=actor, role=viewer,
                entity=f"upload:{upload_id}",
                detail=f"{res['published']} значений за {pid}")
    journal.log(conn, "decide_accept", actor=actor, role=viewer,
                entity=f"upload:{upload_id}", detail=comment or "")
    return {"status": "accepted", "published": res["published"],
            "periodId": res["periodId"],
            "warnings": len([f for f in findings
                             if f["severity"] == "warning"])}
