# -*- coding: utf-8 -*-
"""
Инструмент работы с числами витрины.

Зачем он, если уже есть поиск по документам. Документы объясняют,
но редко содержат именно ту разбивку, которая нужна ректору: где-то
есть «удовлетворённость 88 %», а где-то «78 % по Институту
информационных технологий» — и это разные числа. По витрине такую
разбивку можно посчитать всегда, если данные есть.

Главная оговорка, которую нельзя прятать в промпте: сейчас витрина
демонстрационная. Поэтому каждый результат этого инструмента несёт
метку `demo` и текст-предупреждение, и модель обязана повторить его
в ответе. Пометку ставит код, а не модель: если положиться только на
инструкцию в промпте, то при неудачном запросе метка потеряется.

Про безопасность. Пользовательский вопрос не попадает в текст SQL ни
одним символом — он используется только для подбора метрики и
подразделения, а значения подставляются параметрами. Список полей и
таблиц зашит в код, произвольный SQL из вопроса получить нельзя.
"""
from . import db
from . import retrieve

# Порядок важен: инструмент ничего не находит — он честно отвечает,
# что не понимает вопрос.
NONE_FOUND = {
    "ok": False,
    "reason": "в вопросе не удалось распознать показатель витрины",
    "demo": True,
}

DEMO_NOTICE = ("Число рассчитано по демонстрационной витрине дашборда. "
               "Оно не является отчётными данными университета.")


def _norm(s: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else " " for ch in (s or "")).split()


def find_metric(conn, question: str):
    """Ищет метрику по названию и синонимам. Возвращает строку dim_metric."""
    stems = [retrieve.stem_ru(t) for t in retrieve.tokenize(question)]
    stems = [s for s in stems if len(s) >= 4]
    if not stems:
        return None

    scores = {}
    for row in conn.execute("SELECT * FROM dim_metric"):
        mid = row["metric_id"]
        bucket = {mid: 0}
        for word in _norm(row["title"]):
            bucket[mid] = max(bucket[mid], _score(word, stems))
        for a in conn.execute(
                "SELECT alias FROM metric_alias WHERE metric_id = ?", (mid,)):
            for word in _norm(a["alias"]):
                bucket[mid] = max(bucket[mid], _score(word, stems))
        if bucket[mid] > 0:
            scores[mid] = bucket[mid]

    if not scores:
        return None
    # При нескольких совпадениях берём самое сильное, а не первое
    # попавшееся: у вопроса «отчисления и задолженности» обе метрики
    # найдены, и выбор должен опираться на вес совпадения.
    best = max(scores.items(), key=lambda kv: kv[1])[0]
    row = conn.execute("SELECT * FROM dim_metric WHERE metric_id = ?",
                       (best,)).fetchone()
    return dict(row) if row else None


def _score(word: str, stems) -> int:
    if not word:
        return 0
    if len(word) < 4:
        return 0
    for s in stems:
        if word.startswith(s) or s.startswith(word):
            return 2 if (word == s) else 1
    return 0


def find_org(conn, question: str):
    """Ищет подразделение по названию. None — весь университет."""
    stems = [retrieve.stem_ru(t) for t in retrieve.tokenize(question)]
    best, best_score = None, 0
    for row in conn.execute("SELECT org_id, title FROM dim_org"):
        score = 0
        for word in _norm(row["title"]):
            score = max(score, _score(word, stems))
        if score > best_score:
            best, best_score = row["org_id"], score
    return best if best_score >= 2 else None


def query(question: str, conn, limit_periods: int = 6):
    """Считает по витрине. Всегда возвращает структуру, даже если не нашёл."""
    metric = find_metric(conn, question)
    if not metric:
        return dict(NONE_FOUND)

    org_id = find_org(conn, question)
    mid = metric["metric_id"]

    rows = conn.execute(
        "SELECT f.period_id, p.ord AS pord, p.label, f.value"
        " FROM fact_metric f JOIN dim_period p ON p.period_id = f.period_id"
        " WHERE f.metric_id = ? AND f.org_id = 'ALL'"
        " ORDER BY p.ord DESC LIMIT ?",
        (mid, limit_periods)).fetchall()
    series = [dict(r) for r in reversed(rows)]

    values = [r["value"] for r in series]
    current = values[-1] if values else None
    prev = values[-2] if len(values) > 1 else None

    by_org = []
    if current is not None:
        for r in conn.execute(
                "SELECT f.org_id, d.title, f.value FROM fact_metric f"
                " JOIN dim_org d ON d.org_id = f.org_id"
                " WHERE f.metric_id = ? AND f.org_id <> 'ALL'"
                " AND f.period_id = ? ORDER BY d.ord", (mid, series[-1]["period_id"])):
            by_org.append({"org_id": r["org_id"], "title": r["title"],
                           "value": r["value"]})

    status = db.compute_status(current, metric["dir"], metric["target"]) \
        if current is not None else None
    delta = ((current - prev) / prev * 100) if (current and prev) else None

    return {
        "ok": True,
        "metric": {
            "id": mid, "title": metric["title"], "unit": metric["unit"],
            "target": metric["target"], "dir": metric["dir"], "status": status,
        },
        "org_id": org_id,
        "org_title": next((o["title"] for o in by_org if o["org_id"] == org_id),
                          None) if org_id else None,
        "periods": [{"period_id": s["period_id"], "label": s["label"],
                     "value": s["value"]} for s in series],
        "current": current, "prev": prev, "delta": delta,
        "by_org": by_org,
        "demo": True,
        "notice": DEMO_NOTICE,
        "source": (f"витрина: {metric['title']} · "
                   f"{'все подразделения' if not org_id else org_id} · "
                   f"период {series[-1]['label'] if series else '—'}"),
    }


def render(data: dict) -> str:
    """Готовит блок текста для подстановки в контекст агента."""
    if not data.get("ok"):
        return ("По витрине ничего не посчитано: "
                + str(data.get("reason", "причина неизвестна")) + ".")

    m = data["metric"]
    lines = ["ЧИСЛА ИЗ ВИТРИНЫ ДАШБОРДА (демонстрационные):",
             f"показатель: {m['title']} ({m['unit'] or 'без единиц'})",
             f"текущее значение: {data['current']}"]
    if data["delta"] is not None:
        lines.append(f"изменение к предыдущему периоду: {data['delta']:+.1f} %")
    if m["target"] is not None:
        lines.append(f"целевой порог: {m['target']} · статус: {m['status'] or '—'}")
    if data["by_org"]:
        lines.append("по подразделениям:")
        for o in data["by_org"]:
            lines.append(f"  - {o['title']}: {o['value']}")
    lines.append("ВАЖНО: " + data["notice"])
    return "\n".join(lines)