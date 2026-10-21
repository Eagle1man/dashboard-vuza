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

import re

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


# --- Общие слова: сами по себе метрику не выбирают -----------------------
#
# Разбор придумал не тот, кто писал код. Вопросы ректора «сколько
# студентов» и «какая успеваемость студентов» — разные, а слово
# «студентов» в них одно и то же. Прежний подсчёт брал по одному
# лучшему совпадению на метрику, поэтому «студентов» тянуло вопрос в
# «Иностранных студентов» с тем же весом, с каким «успеваемость»
# тянуло в «Успеваемость», — и выигрывала та метрика, что стояла в
# справочнике раньше. Это не мелочь: не тот показатель в ответе хуже,
# чем никакого.
#
# Теперь общие слова метрику не выбирают — они лишь уточняют уже
# выбранную. Если кроме них ничего не нашлось, вопрос читается как
# «про обучающихся», и ответом становится контингент.
GENERIC_ROOTS = ["студент", "обучающ", "человек"]

# Слова-заполнители: не значат ни для выбора, ни для уточнения.
FILLER_ROOTS = ["какая", "какой", "какие", "какого", "скольк", "всег",
                "общее", "университет", "вуз", "институт", "сейчас",
                "также", "более", "менее", "наш", "ваш", "там", "тут",
                "дайте", "покаж", "скаж", "расскаж", "какие"]

# Слова, которые в названии подразделения ничего не различают: они
# есть в названии каждой кафедры и уводят выбор на первую попавшуюся.
ORG_FILLER = ["кафедр", "академ"]

# Сокращения: ФОТ, НИР, ЕГЭ, СПО, ВПР, LMS, ЭИОС. Это язык ректора, а
# не опечатка, и отбрасывать их вместе с короткими словами нельзя:
# на «какая доля ФОТ» без сокращения вопрос остаётся без «доли» и
# уезжает на любой показатель с этим словом в названии.
_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")


def _is_abbrev(token: str) -> bool:
    """Сокращение из заглавных букв: ФОТ, НИР, ЕГЭ, СПО, ВПР, LMS."""
    if len(token) < 2 or not any(ch.isalpha() for ch in token):
        return False
    return token == token.upper()


def _is_generic(stem: str) -> bool:
    return any(stem.startswith(g) or g.startswith(stem) for g in GENERIC_ROOTS)


def _is_filler(stem: str) -> bool:
    return any(stem.startswith(f) or f.startswith(stem) for f in FILLER_ROOTS)


def _match(word: str, stem: str) -> int:
    """Совпадение слова названия с основой слова вопроса.

    2 — точное совпадение основ, 1 — одна основа является началом
    другой («выпуск» / «выпускники»), 0 — несовпадение.

    Длину слова здесь не проверяем: отбор по длине уже сделал тот,
    кто собрал слова (см. _words). Повторная проверка выбрасывала
    сокращения вроде «ФОТ» уже после того, как они были признаны
    значимыми.
    """
    if not word or not stem:
        return 0
    if word == stem:
        return 2
    if word.startswith(stem) or stem.startswith(word):
        return 1
    return 0


def _words(text: str):
    """Значимые слова текста: с основами, без чисел и общего.

    Регистр здесь важен: по нему видно сокращение. «ФОТ» — три буквы,
    но вопрос «какая доля ФОТ» без него теряет половину смысла.
    """
    out = set()
    for tok in _TOKEN_RE.findall(text or ""):
        if tok.isdigit():
            continue
        st = retrieve.stem_ru(tok)
        if len(st) < 4 and not _is_abbrev(tok):
            continue
        out.add(st)
    return out


def find_metric(conn, question: str):
    """Ищет метрику по названию и синонимам. Возвращает строку dim_metric.

    Порядок решения такой:

    1. Собираем значимые слова вопроса: отдельно те, что несут смысл
       («успеваемость»), отдельно общие («студентов»).
    2. Для каждой метрики считаем, сколько её слов нашлось по
       значимым словам вопроса. Считаем по КАЖДОМУ слову названия, а не
       по лучшему совпадению: раньше метрика «Успеваемость» и
       «Иностранных студентов» давали по два балла за одно и то же
       слово, и выбор решался порядком строк в справочнике.
    3. Общие слова дают балл только тем метрикам, у которых они тоже
       есть, — и служат уточнением, а не выбором.
    4. При равенстве смотрим, сколько слов названия метрики вопрос не
       объяснил: чем меньше, тем точнее попадание.
    """
    q_stems = []
    for t in retrieve.tokenize(question):
        st = retrieve.stem_ru(t)
        if st.isdigit():
            continue
        if len(st) < 4 and not _is_abbrev(t):
            continue
        if _is_filler(st):
            continue
        q_stems.append(st)
    if not q_stems:
        return None

    specific = [s for s in q_stems if not _is_generic(s)]
    generic = [s for s in q_stems if _is_generic(s)]

    best_key, best_mid = None, None
    for row in conn.execute("SELECT * FROM dim_metric"):
        mid = row["metric_id"]
        title_w = _words(row["title"])
        alias_w = set()
        for a in conn.execute(
                "SELECT alias FROM metric_alias WHERE metric_id = ?", (mid,)):
            alias_w |= _words(a["alias"])
        all_w = title_w | alias_w

        # Значимые слова: каждое слово названия и синонимов сопоставляется
        # с лучшим из значимых слов вопроса.
        matched, weight = set(), 0
        for w in all_w:
            if _is_filler(w) or _is_generic(w):
                continue
            best = 0
            for s in specific:
                sc = _match(w, s)
                if sc > best:
                    best = sc
            if best:
                matched.add(w)
                weight += best

        # Общие слова: метрику не выбирают, но уточняют.
        gen_hits = 0
        for w in all_w:
            if not _is_generic(w):
                continue
            for s in generic:
                if _match(w, s):
                    gen_hits += 1
                    break

        if weight == 0 and gen_hits == 0:
            continue

        # Сколько слов названия осталось необъяснёнными: у «Контингента
        # обучающихся» не объяснено одно слово, у «Контингента
        # бакалавриата» — два, и на вопрос про численность обучающихся
        # первое попадание точнее.
        unexplained = sum(
            1 for w in title_w
            if not _is_filler(w) and not _is_generic(w) and w not in matched)

        key = (weight, gen_hits, -unexplained)
        if best_key is None or key > best_key:
            best_key, best_mid = key, mid

    if not best_mid:
        return None
    row = conn.execute("SELECT * FROM dim_metric WHERE metric_id = ?",
                       (best_mid,)).fetchone()
    return dict(row) if row else None


def find_org(conn, question: str):
    """Ищет подразделение по названию. None — весь университет."""
    q_stems = []
    for t in retrieve.tokenize(question):
        st = retrieve.stem_ru(t)
        if st.isdigit():
            continue
        if len(st) < 4 and not _is_abbrev(t):
            continue
        q_stems.append(st)
    best, best_score = None, 0
    for row in conn.execute("SELECT org_id, title FROM dim_org"):
        score = 0
        for w in _words(row["title"]):
            if w in ORG_FILLER:
                continue
            for s in q_stems:
                sc = _match(w, s)
                if sc > score:
                    score = sc
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