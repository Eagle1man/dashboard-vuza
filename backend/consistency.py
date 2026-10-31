# -*- coding: utf-8 -*-
"""
Объяснение проверок большой языковой моделью (ТЗ §1.4, §13).

Отделено от детерминированных проверок: checks.py решает, правильно ли
число, а здесь модель объясняет проверяющему, что это значит и что
делать. Если большой модели нет (по договорённости она подключается
на ВМ), экран проверки честно показывает причину — детерминированные
проверки и публикация при этом работают.

Результат кешируется в parse_card.explanation: повторное открытие
экрана не платит по времени модели.
"""
import json
import threading
import time

from . import checks
from . import db
from . import llm

SYSTEM = (
    "Ты — аналитик дашборда ректора вуза. Тебе дают карточку загрузки и "
    "список автоматических проверок. Напиши по-русски, короткими абзацами "
    "(не более 8): что именно не так, чем это грозит публикации числа и что "
    "конкретно сделать проверяющему. Не повторяй список проверок дословно и "
    "не выдумывай факты о документе, которых нет в карточке."
)


def _context(conn, upload_id: int) -> str | None:
    meta = conn.execute(
        """SELECT u.original_name, u.extension, u.size_bytes, dm.title,
                  dm.purpose, dm.period, dm.responsible, dm.metric_ids
             FROM upload u LEFT JOIN doc_meta dm ON dm.doc_id = u.doc_id
            WHERE u.upload_id = ?""", (upload_id,)).fetchone()
    if not meta:
        return None
    card = checks.load_card(conn, upload_id) or {}
    rows = conn.execute(
        "SELECT check_code, severity, message FROM upload_check"
        " WHERE upload_id = ? ORDER BY id", (upload_id,)).fetchall()

    payload = {
        "файл": meta["original_name"],
        "тип": meta["extension"],
        "анкета": {
            "что": meta["title"], "зачем": meta["purpose"],
            "период": meta["period"], "ответственный": meta["responsible"],
        },
        "карточка": {
            "тип_документа": card.get("docType"),
            "период": card.get("period"),
            "сопоставления": [
                {"показатель": m.get("metricTitle") or m.get("metricId"),
                 "значение": m.get("value"),
                 "источник": m.get("source")}
                for m in (card.get("mapping") or [])[:20]],
            "не_сопоставлено": [u.get("label")
                                for u in (card.get("unmapped") or [])[:10]],
        },
        "проверки": [{"код": r["check_code"], "серьёзность": r["severity"],
                      "текст": r["message"]} for r in rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def explain_sync(upload_id: int) -> dict:
    """Запрашивает объяснение у большой модели и сохраняет результат."""
    # Не тратим токены на кешированные объяснения
    conn_c = db.connect()
    try:
        cached_res = cached(conn_c, upload_id)
        if cached_res.get("available"):
            conn_c.close()
            return cached_res
    except Exception:
        pass
    try:
        conn_c.close()
    except Exception:
        pass

    st = llm.role_status("large")
    if not st.get("installed"):
        reason = (f"большая модель {st['model']} не подключена"
                  + (" — работает малая модель" if st.get("fallback") else
                     " (подключается при размещении на ВМ)"))
        if st.get("fallback"):
            return _run(upload_id, allow_fallback=True)
        return {"available": False, "reason": reason}

    if st.get("fallback"):
        return _run(upload_id, allow_fallback=True)
    return _run(upload_id)


def _run(upload_id: int, allow_fallback: bool = False) -> dict:
    conn = db.connect()
    try:
        ctx = _context(conn, upload_id)
        if ctx is None:
            return {"available": False, "reason": "загрузка не найдена"}
        card_row = conn.execute(
            "SELECT model FROM parse_card WHERE upload_id = ?",
            (upload_id,)).fetchone()

        started = time.time()
        text, meta = llm.chat(
            SYSTEM,
            "Данные карточки (JSON):\n" + ctx,
            role="large", purpose="consistency", num_predict=280)
        if not meta.get("ok"):
            conn.close()
            return {"available": False,
                    "reason": meta.get("reason") or "модель недоступна",
                    "model": meta.get("model")}
        ms = int((time.time() - started) * 1000)
        conn.execute(
            "UPDATE parse_card SET explanation = ?, expl_model = ?,"
            " expl_ms = ?, explained_at = ? WHERE upload_id = ?",
            (text, f"large:{meta.get('model')}", ms,
             time.strftime("%Y-%m-%d %H:%M:%S"), upload_id))
        conn.commit()
        conn.close()
        return {"available": True, "text": text,
                "model": meta.get("model"), "ms": ms}
    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass
        return {"available": False, "reason": type(exc).__name__}


def explain_async(upload_id: int) -> None:
    """Запускает объяснение в фоне (приём файла не должен ждать модели)."""
    def _worker():
        try:
            explain_sync(upload_id)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True,
                     name=f"explain-{upload_id}").start()


def cached(conn, upload_id: int) -> dict:
    """Что уже объяснено (без обращения к модели)."""
    row = conn.execute(
        "SELECT explanation, expl_model, expl_ms, explained_at FROM parse_card"
        " WHERE upload_id = ?", (upload_id,)).fetchone()
    if not row or not row["explanation"]:
        return {"available": False, "reason": "ещё не запрашивалось"}
    return {"available": True, "text": row["explanation"],
            "model": row["expl_model"], "ms": row["expl_ms"],
            "explainedAt": row["explained_at"]}
