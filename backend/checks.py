# -*- coding: utf-8 -*-
"""
Детерминированные проверки загрузки (ТЗ §10).

Проверки не используют модель: они работают и при недоступном ИИ, и их
результат воспроизводим. Каждая проверка даёт код, серьёзность и текст:

  severity = 'error'   — блокирует публикацию (кнопка «Принять» выключена)
  severity = 'warning' — показывается, но публикация возможна

Коды проверок (ТЗ §10):
  questionnaire_incomplete — не заполнены обязательные поля анкеты
  value_missing            — для показателя не задано число
  value_not_number         — значение не разбирается как число
  duplicate_value          — значение уже подтверждено другой загрузкой
  sum_mismatch             — сумма составляющих не совпадает с итогом
  period_new               — периода нет в справочнике, он будет создан
  period_mismatch          — период в файле отличается от периода анкеты
  range_out                — значение вне диапазона каталога (если задан)
  no_source_ref            — число не привязано к фрагменту документа
  unmapped_values          — есть числа, не сопоставленные с показателями
  no_values                — нечего публиковать (пустая карточка)
  anomaly_x3               — значение изменилось более чем в 3 раза
  model_unavailable        — карточка построена без модели
  raw_truncated            — сырьё обрезано по лимиту
"""
import json
import re
from decimal import Decimal

from . import config
from . import db
from . import numeric

# «Итого», «Всего», «Валовая прибыль» — нет: только итоговые подписи.
_TOTAL_RE = re.compile(r"(?i)^\s*(итог|итого|всего|всё\s*всего|налог|"
                       r"итог\s*по\s*строке)\b")


def period_candidate(conn, label: str | None) -> tuple[str | None, bool]:
    """(period_id, есть_ли_уже_в_справочнике) без записи в базу.

    Повторяет логику uploads._ensure_period, но не создаёт период:
    проверка не должна менять данные.
    """
    if not label:
        return None, False
    row = conn.execute("SELECT period_id FROM dim_period WHERE label = ?",
                       (label,)).fetchone()
    if row:
        return row["period_id"], True
    m = re.search(r"(\d{4})[-/](\d{1,2})", label)
    if m:
        pid = f"{m.group(1)}-{int(m.group(2)):02d}"
        exists = bool(conn.execute(
            "SELECT 1 FROM dim_period WHERE period_id = ?", (pid,)).fetchone())
        return pid, exists
    m = re.fullmatch(r"\s*(\d{4})\s*(г\.?)?", label)
    if m:
        pid = f"{m.group(1)}"
        exists = bool(conn.execute(
            "SELECT 1 FROM dim_period WHERE period_id = ?", (pid,)).fetchone())
        return pid, exists
    return None, False


def load_card(conn, upload_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM parse_card WHERE upload_id = ?",
                     (upload_id,)).fetchone()
    if not r:
        return None
    card = dict(r)
    for key, out in (("values_json", "values"), ("mapping_json", "mapping"),
                     ("unmapped_json", "unmapped")):
        try:
            card[out] = json.loads(card.get(key) or "[]")
        except json.JSONDecodeError:
            card[out] = []
    # Ключи, которыми пользуются все модули (клиентские, camelCase).
    card["docType"] = card.pop("doc_type", None)
    card["periodId"] = card.pop("period_id", None)
    card["status"] = card.pop("status", None)
    card["model"] = card.pop("model", None)
    card["attempts"] = card.pop("attempts", None)
    card["parseMs"] = card.pop("parse_ms", None)
    card["parseError"] = card.pop("parse_error", None)
    card["editedAt"] = card.pop("edited_at", None)
    card["editedBy"] = card.pop("edited_by", None)
    return card


def save_card(conn, upload_id: int, card: dict, status: str | None = None,
              edited_by: str | None = None) -> None:
    """Сохраняет исправленную карточку. Сырьё (upload_raw) не трогает."""
    conn.execute(
        "UPDATE parse_card SET values_json=?, mapping_json=?, unmapped_json=?,"
        " doc_type=?, period=?, period_id=?, status=?, edited_at=?, edited_by=?"
        " WHERE upload_id=?",
        (json.dumps(card.get("values", []), ensure_ascii=False),
         json.dumps(card.get("mapping", []), ensure_ascii=False),
         json.dumps(card.get("unmapped", []), ensure_ascii=False),
         card.get("docType"), card.get("period"), card.get("periodId"),
         status or card.get("status") or "corrected",
         _now(), edited_by, upload_id))
    conn.commit()

def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _existing_value(conn, period_id: str, metric_id: str,
                    org_id: str = "ALL") -> dict | None:
    """Текущее значение витрины + последний источник этого значения."""
    row = conn.execute(
        """SELECT f.value, f.value_exact, f.status,
                  (SELECT prov.source_upload_id FROM fact_provenance prov
                    WHERE prov.period_id = f.period_id
                      AND prov.metric_id = f.metric_id
                      AND prov.org_id = f.org_id
                    ORDER BY prov.prov_id DESC LIMIT 1) AS src_upload,
                  (SELECT prov.source_file_name FROM fact_provenance prov
                    WHERE prov.period_id = f.period_id
                      AND prov.metric_id = f.metric_id
                      AND prov.org_id = f.org_id
                    ORDER BY prov.prov_id DESC LIMIT 1) AS src_file
             FROM fact_metric f
            WHERE f.period_id = ? AND f.metric_id = ? AND f.org_id = ?""",
        (period_id, metric_id, org_id)).fetchone()
    return dict(row) if row else None


def _previous_value(conn, period_id: str, metric_id: str,
                    org_id: str = "ALL") -> tuple[float | None, str | None]:
    """Значение за предыдущий период (по ord) — база для проверки аномалий."""
    cur = conn.execute("SELECT ord FROM dim_period WHERE period_id = ?",
                       (period_id,)).fetchone()
    if not cur:
        return None, None
    row = conn.execute(
        """SELECT f.value, p.label FROM fact_metric f
             JOIN dim_period p ON p.period_id = f.period_id
            WHERE f.metric_id = ? AND f.org_id = ? AND p.ord < ?
            ORDER BY p.ord DESC LIMIT 1""", (metric_id, org_id, cur["ord"])
    ).fetchone()
    if not row:
        return None, None
    return float(row["value"]), row["label"]


def _check_sums(conn, upload_id: int, findings: list) -> None:
    """Сумма строк против строки «Итого» (xlsx/xls/csv, по колонкам)."""
    rows = conn.execute(
        """SELECT sheet, row_no, label, text FROM upload_raw
            WHERE upload_id = ? AND kind = 'row' AND sheet IS NOT NULL
            ORDER BY sheet, row_no""", (upload_id,)).fetchall()
    by_sheet: dict[str, list] = {}
    for r in rows:
        by_sheet.setdefault(r["sheet"], []).append(dict(r))

    tol = config.SUM_TOLERANCE
    for sheet, items in by_sheet.items():
        for idx, item in enumerate(items):
            if not item["text"] or not _TOTAL_RE.match(item["label"] or "") \
                    and not _TOTAL_RE.match(
                        (item["text"].split(" | ")[0] or "")):
                continue
            cells = (item["text"] or "").split(" | ")
            total_at = None
            total_val = None
            for i, c in enumerate(cells):
                d = numeric.to_decimal(c)
                if d is not None:
                    total_at, total_val = i, d
                    break
            if total_at is None or total_val is None:
                continue
            # Собираем составляющие: строки выше с числом в той же колонке.
            parts = []
            for prev in reversed(items[:idx]):
                if item["row_no"] and prev["row_no"] and \
                        item["row_no"] - prev["row_no"] > 30:
                    break
                pcells = (prev["text"] or "").split(" | ")
                if len(pcells) <= total_at:
                    continue
                label0 = (pcells[0] or "").strip()
                if not label0 or _TOTAL_RE.match(label0):
                    continue
                d = numeric.to_decimal(pcells[total_at])
                if d is None:
                    continue
                # Годы в шапке таблицы не суммируются.
                if re.fullmatch(r"(19|20)\d{2}", pcells[total_at].strip()):
                    continue
                parts.append(d)
            if len(parts) < 2:
                continue
            s = sum(parts, Decimal(0))
            base = max(abs(float(total_val)), abs(float(s)), 1.0)
            if abs(float(s) - float(total_val)) > tol * base:
                findings.append({
                    "check_code": "sum_mismatch", "severity": "error",
                    "message": (f"Лист «{sheet}»: сумма {len(parts)} строк "
                                f"= {numeric.exact(s)}, а в строке «Итого» "
                                f"{numeric.exact(total_val)} (допуск "
                                f"{tol:.1%})"),
                    "field": f"{sheet}!строка {item['row_no']}",
                    "value_a": numeric.exact(s),
                    "value_b": numeric.exact(total_val)})


def run(conn, upload_id: int) -> list[dict]:
    """Запускает все проверки, перезаписывает rows в upload_check."""
    conn.execute("DELETE FROM upload_check WHERE upload_id = ?",
                 (upload_id,))
    findings: list[dict] = []

    def add(code, severity, message, field=None, a=None, b=None):
        findings.append({"check_code": code, "severity": severity,
                         "message": message, "field": field,
                         "value_a": None if a is None else str(a),
                         "value_b": None if b is None else str(b)})

    meta = conn.execute(
        """SELECT u.*, dm.title AS m_title, dm.purpose, dm.period,
                  dm.frequency, dm.responsible, dm.access_level,
                  dm.metric_ids, dm.confirmed
             FROM upload u
             LEFT JOIN doc_meta dm ON dm.doc_id = u.doc_id
            WHERE u.upload_id = ?""", (upload_id,)).fetchone()
    if not meta:
        return findings
    meta = dict(meta)

    card = load_card(conn, upload_id)
    card = card or {"mapping": [], "unmapped": [], "values": [],
                    "period": meta.get("period"), "periodId": None,
                    "parseError": None}

    # --- обязательные поля анкеты -------------------------------------
    missing = [label for label, key in (
        ("что это за данные", "m_title"), ("зачем данные", "purpose"),
        ("период", "period"), ("ответственный", "responsible"))
        if not str(meta.get(key) or "").strip()]
    if missing:
        add("questionnaire_incomplete", "error",
            "Не заполнены обязательные поля анкеты: " + ", ".join(missing))

    # --- состояние модели ---------------------------------------------
    if card.get("parseError"):
        add("model_unavailable", "warning",
            "Карточка построена без модели: " + str(card["parseError"]))

    # --- период --------------------------------------------------------
    period_label = card.get("period") or meta.get("period")
    pid, exists = period_candidate(conn, period_label)
    if period_label and not exists:
        add("period_new", "warning",
            f"Период «{period_label}» отсутствует в справочнике — "
            "он будет создан при публикации")
    if card.get("period") and meta.get("period") and \
            card["period"].strip() != str(meta["period"]).strip():
        add("period_mismatch", "warning",
            f"В файле указан период «{card['period']}», в анкете — "
            f"«{meta['period']}». Значения будут опубликованы за период "
            "из анкеты, если период не изменён на экране проверки",
            field="period", a=card["period"], b=meta["period"])

    # --- значения --------------------------------------------------------
    mapping = card.get("mapping") or []
    for m in mapping:
        mid = str(m.get("metricId") or "")
        label = str(m.get("label") or m.get("metricTitle") or mid)
        raw_val = m.get("value")
        if raw_val is None or str(raw_val).strip() == "":
            add("value_missing", "warning",
                f"«{label}»: число не задано — внесите его на экране "
                "проверки или уберите строку",
                field=mid)
            continue
        d = numeric.to_decimal(raw_val)
        if d is None:
            add("value_not_number", "error",
                f"«{label}»: значение «{raw_val}» не является числом",
                field=mid, a=raw_val)
            continue

        # Диапазон каталога задаёт владелец каталога; пусто — не проверяем.
        met = conn.execute(
            "SELECT min_val, max_val, unit FROM dim_metric WHERE metric_id = ?",
            (mid,)).fetchone()
        if met and (met["min_val"] is not None or met["max_val"] is not None):
            if met["min_val"] is not None and d < Decimal(str(met["min_val"])):
                add("range_out", "warning",
                    f"«{label}»: {numeric.exact(d)} меньше нижней границы "
                    f"{met['min_val']} каталога", field=mid, a=numeric.exact(d),
                    b=met["min_val"])
            if met["max_val"] is not None and d > Decimal(str(met["max_val"])):
                add("range_out", "warning",
                    f"«{label}»: {numeric.exact(d)} больше верхней границы "
                    f"{met['max_val']} каталога", field=mid, a=numeric.exact(d),
                    b=met["max_val"])

        if not m.get("rawRef"):
            add("no_source_ref", "warning",
                f"«{label}»: число не привязано к фрагменту документа — "
                "провенанс будет неполным", field=mid)

        if not pid:
            continue
        ex = _existing_value(conn, pid, mid)
        if ex:
            if ex.get("src_upload") and int(ex["src_upload"]) != upload_id:
                add("duplicate_value", "error",
                    f"«{label}»: за этот период значение уже подтверждено "
                    f"другой загрузкой ({ex.get('src_file') or 'файл'} — "
                    f"{ex['value']}). При принятии подтвердите перезапись",
                    field=mid, a=ex["value"], b=numeric.exact(d))
            prev, prev_label = _previous_value(conn, pid, mid)
            if prev not in (None, 0):
                ratio = abs(float(d)) / abs(prev)
                if ratio > 3 or ratio < 1 / 3:
                    add("anomaly_x3", "warning",
                        f"«{label}»: значение {numeric.exact(d)} отличается "
                        f"от «{prev_label}» ({prev:g}) более чем в 3 раза — "
                        "проверьте период и единицы", field=mid,
                        a=numeric.exact(d), b=f"{prev:g}")

    # --- прочее ----------------------------------------------------------
    if not mapping:
        add("no_values", "warning",
            "В карточке нет чисел — документ будет принят без публикации "
            "значений (только источник для поиска)")
    unmapped = card.get("unmapped") or []
    if unmapped:
        names = ", ".join(str(u.get("label") or u.get("text") or "?")
                          for u in unmapped[:5])
        more = f" и ещё {len(unmapped) - 5}" if len(unmapped) > 5 else ""
        add("unmapped_values", "warning",
            f"Не сопоставлено с показателями: {names}{more} "
            "(оставьте как есть или сопоставьте вручную)")

    raw_n = conn.execute("SELECT COUNT(*) AS n FROM upload_raw WHERE upload_id = ?",
                         (upload_id,)).fetchone()["n"]
    if raw_n >= config.MAX_RAW_ROWS:
        add("raw_truncated", "warning",
            "Содержимое файла сохранено не полностью (лимит сырья) — "
            "часть чисел могла не попасть в карточку")

    _check_sums(conn, upload_id, findings)

    for f in findings:
        conn.execute(
            "INSERT INTO upload_check(upload_id, check_code, severity,"
            " message, field, value_a, value_b) VALUES(?,?,?,?,?,?,?)",
            (upload_id, f["check_code"], f["severity"], f["message"],
             f.get("field"), f.get("value_a"), f.get("value_b")))
    conn.commit()
    return findings
