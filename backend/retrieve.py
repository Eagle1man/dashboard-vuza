# -*- coding: utf-8 -*-
"""
Поиск по корпусу: FTS5 (слова) + вектор (смысл), слияние RRF.

Почему гибрид, а не только вектор. В документах вуза много точных
терминов: «линёйка», аббревиатуры кафедр, «ФГОС 3++», номера приказов.
Чистый векторный поиск на русском такие строки регулярно теряет —
он ищет близость по смыслу, а «приказ № 187-р» ни с чему близко
не смыслово. Словарный поиск, наоборот, не поймёт «отсев», если в
документе написано «отчисления». Слияние RRF берёт лучшее от обоих
и не требует подбирать веса: ранги объединяются, а не складываются
несопоставимые числа.

Бонус: лёгкий стеммер для русского при формировании запроса.
Индекс хранит текст как есть, а запрос идёт по основе с префиксом
(«отчислен*» находит отчисления/отчисление/отчислено) — это работает
без переиндексации и без внешнего словаря.
"""
import re

import numpy as np

from . import config
from . import embed

# Суффиксы, которые отрезаем. Порядок — от длинных к коротким.
_SUFFIXES = [
    "ированием", "ированию", "ирования", "ированный",
    "ованиями", "ованиях", "ование", "ований", "ования",
    "ами", "ями", "ому", "ему", "ыми", "ими", "ать", "ять", "еть", "ить",
    "ов", "ев", "ей", "ой", "ий", "ый", "ая", "яя", "ое", "ее",
    "ы", "и", "е", "у", "ю", "я", "а", "о", "ь", "й",
]

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")

# Точное название в кавычках — сильнейший признак, какой именно вуз
# имеется в виду. Без него у официальных названий («Федеральное
# государственное бюджетное образовательное учреждение…») поиск
# цепляется за общий префикс и выдаёт чужую строку таблицы.
_QUOTED_RE = re.compile(r"«([^»]{6,})»")
_YEAR_RE = re.compile(r"\b(20\d\d)\b")

# Слова, которые не несут смысла при поиске по документам
_STOPWORDS = {
    # вопросительные
    "и", "в", "на", "с", "по", "для", "не", "что", "как", "какая",
    "какой", "какие", "какого", "почему", "зачем", "где", "когда",
    "сколько", "the", "of", "and", "to", "a", "in", "is", "at", "by", "or",
    # служебные
    "при", "их", "его", "она", "они", "так", "все", "быть", "был",
    "была", "было", "были", "если", "чтобы", "также", "более",
    # институциональные: есть почти в каждом документе и потому
    # поднимают в выдаче любой файл. Вопросы ректора это слово
    # содержат почти всегда, а различать надо по существу.
    "университет", "университета", "университете", "университетом",
    "университетский", "вуз", "вузы", "вузе",
}


def stem_ru(token: str) -> str:
    t = token.lower()
    for suf in _SUFFIXES:
        if len(t) - len(suf) >= 4 and t.endswith(suf):
            return t[:-len(suf)]
    return t


def tokenize(query: str):
    return _TOKEN_RE.findall(query or "")


def query_stems(query: str):
    """Основы значимых слов вопроса — по ним считается покрытие."""
    stems = []
    for tok in tokenize(query):
        low = tok.lower()
        if low in _STOPWORDS or len(low) < 2:
            continue
        stem = stem_ru(low)
        stems.append(stem if len(stem) >= 4 else low)
    seen, uniq = set(), []
    for s in stems:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def fts_expression(query: str) -> str:
    """Собирает безопасное выражение FTS5 из вопроса.

    Каждый токен берётся в кавычки: иначе вопрос с двоеточием,
    скобкой или минусом («план: приём», «бюджет - 2026») роняет
    парсер FTS5, и поиск падает целиком.
    """
    return " OR ".join(
        (f'"{s}"*' if len(s) >= 4 else f'"{s}"') for s in query_stems(query))


# Совпадение в названии документа — сильный признак тематической
# близости: в названии «Положение об аттестации…» слово «аттестация»
# стоит один раз, но означает ровно то, о чём спрашивают.
# В теле абзаца то же слово может встретиться десять раз по разным
# поводам. Поэтому покрытие считается с весами по полям.
FIELD_WEIGHTS = (("title", 3.0), ("heading", 2.0), ("text", 1.0))


def _norm_field(text: str) -> str:
    """Нормализация для точного сравнения названий.

    Кавычки в источниках разные: «ёлочки», "лапки", иногда немецкие.
    Человек в вопросе ставит любые — поэтому перед сравнением все виды
    кавычек приводятся к одной.
    """
    s = str(text or "").lower()
    for q in "«»„“”\"'":
        s = s.replace(q, '"')
    return re.sub(r"\s+", " ", s).strip()


def weighted_coverage(stems, title, heading, text) -> float:
    """Доля вопроса, покрытая фрагментом, с весами по полям."""
    if not stems:
        return 0.0
    fields = {
        "title": (title or "").lower(),
        "heading": (heading or "").lower(),
        "text": (text or "").lower(),
    }
    gained = 0.0
    for stem in stems:
        best = 0.0
        for name, weight in FIELD_WEIGHTS:
            if stem in fields[name]:
                best = max(best, weight)
        gained += best
    return gained / (FIELD_WEIGHTS[0][1] * len(stems))


def _block_clause(blocks):
    """SQL-условие и параметры для ограничения по блокам дашборда.

    doc.block_id может быть NULL (документ не опознан как относящийся
    к блоку). Такие документы при фильтре отбрасываются: иначе
    «специалист по финансам» получал бы в контекст всё подряд,
    и в специалистах не было бы никакого смысла.
    """
    if not blocks:
        return "", []
    marks = ",".join("?" * len(blocks))
    return f" AND d.doc_id IN (SELECT doc_id FROM doc_block WHERE block_id IN ({marks}))", \
        list(blocks)


def _demo_clause(include_demo):
    """Отбрасывает демонстрационные документы, если есть настоящие.

    Зачем. Демо-документы содержат ВЫМЫШЛЕННЫЕ числа: в демонстрационном
    «Положении о приёме» написано, что студентов 20 800, тогда как в
    отчёте о самообследовании их 2 946. Пока демо-документы попадают в
    выдачу, модель приводит «20 800» как число университета — а модель
    на 1.5b пометку «демо» пропускает.

    Отбрасывает их код, а не просьба модели: на это правило полагаться
    нельзя.

    Демо-документы остаются в корпусе для установки, где настоящих
    документов нет: тогда они единственные, и показывать их полезнее,
    чем не показывать ничего. Но как только в базе появляется хотя бы
    один настоящий документ, вымышленные числа из ответа исчезают.
    """
    if include_demo:
        return "", []
    return " AND d.is_demo = 0", []


def _has_official_docs(conn):
    row = conn.execute(
        "SELECT 1 FROM doc WHERE is_demo = 0 LIMIT 1").fetchone()
    return bool(row)


def _fts_search(conn, query: str, limit: int, blocks=None, include_demo=True):
    expr = fts_expression(query)
    if not expr:
        return [], 0
    clause, params = _block_clause(blocks)
    demo_clause, demo_params = _demo_clause(include_demo)
    try:
        rows = conn.execute(
            "SELECT c.chunk_id, bm25(chunk_fts) AS score"
            " FROM chunk_fts c"
            " JOIN doc_chunk dc ON dc.chunk_id = c.chunk_id"
            " JOIN doc d ON d.doc_id = dc.doc_id"
            " WHERE chunk_fts MATCH ?" + clause + demo_clause +
            " ORDER BY score LIMIT ?",
            [expr] + params + demo_params + [limit]).fetchall()
    except Exception:
        # FTS5 не должен ронять поиск целиком: ловим и идём только по вектору
        return [], 0
    return [(int(r["chunk_id"]), float(r["score"])) for r in rows], len(expr.split(" OR "))


def _vector_search(conn, question: str, limit: int, blocks=None, include_demo=True):
    """Возвращает (список (chunk_id, cos), backend, max_cos)."""
    # Если модели нет, векторный канал всё равно будет отброшен при
    # слиянии — незачем грузить в память все векторы и считать
    # произведение впустую на каждом вопросе.
    if not embed.ollama_available():
        return [], "ollama-offline", 0.0

    clause, params = _block_clause(blocks)
    demo_clause, demo_params = _demo_clause(include_demo)
    rows = conn.execute(
        "SELECT v.chunk_id, v.dim, v.vec FROM chunk_vec v"
        " JOIN doc d ON d.doc_id = v.doc_id WHERE 1=1" + clause + demo_clause,
        params + demo_params).fetchall()
    if not rows:
        return [], "none", 0.0
    dim = int(rows[0]["dim"])
    if any(int(r["dim"]) != dim for r in rows):
        # в индексе смешались размерности (сменилась модель) —
        # векторное сравнение бессмысленно, но словарное ещё работает
        return [], "mixed-dim", 0.0

    matrix = np.vstack([embed.from_blob(r["vec"], dim) for r in rows])
    q, backend = embed.embed_texts([question])
    if q.size == 0 or q.shape[1] != dim:
        return [], backend or "none", 0.0
    scores = matrix @ q[0]
    order = np.argsort(-scores)[:limit]
    return ([(int(rows[i]["chunk_id"]), float(scores[i])) for i in order],
            backend, float(scores[order[0]]) if len(order) else 0.0)


def search(conn, question: str, top_k: int = None, pool: int = None,
            blocks=None):
    """Возвращает список найденных кусков со слиянием рангов RRF.

    Три решения, без которых гибридный поиск работает хуже, чем
    один только словарный:

    1. Оба канала обрезаются до ОДИНАКОВОЙ длины до слияния.
       При разной длине список, который вернул больше записей,
       получает больше суммарного веса просто за счёт количества,
       и его шум перевешивает точные совпадения другого канала.

    2. Каналу-вектору нельзя верить, если он считается в заглушке.
       Эмбеддинг hash-fallback не несёт смысла — это шум, но если
       дать ему половину веса, он перемешивает выдачу и портит
       результат. Тогда он отключается целиком, а статус пишется
       в stats, чтобы по /api/health было видно, что система живёт
       в аварийном режиме, а не «работает как обычно».

    3. Порог схожести. Даже у настоящих эмбеддингов максимум
       косинуса может быть низким — значит вопрос не про этот
       корпус. Лучше честно вернуть пусто и сказать «не найдено»,
       чем подсунуть нерелевантные фрагменты.
    """
    top_k = top_k or config.RETRIEVE_TOP_K
    pool = pool or max(top_k * 3, config.RETRIEVE_POOL)
    # Демо-документы показываются, только если настоящих нет: в корпусе
    # с настоящими документами вымышленные числа в ответе опаснее, чем
    # отсутствие ответа.
    include_demo = not _has_official_docs(conn)

    fts, n_terms = _fts_search(conn, question, pool, blocks=blocks,
                               include_demo=include_demo)
    vec, vec_backend, max_cos = _vector_search(conn, question, pool, blocks=blocks,
                                                include_demo=include_demo)

    # Отдельный канал «субъект вопроса». В сравнении «ИУБиП против вуза X»
    # длинное официальное название X приносит десяток совпавших слов, а
    # «ИУБиП» — одно: строка университета уходила на 176-е место и не
    # попадала в пул. Канал гарантирует, что субъект вообще есть среди
    # кандидатов; окончательный порядок всё равно решают покрытие и
    # надбавки, а не сам факт упоминания.
    subject_hits = []
    question_low = question.lower()
    subject_alias = next((a for a in config.SUBJECT_ALIASES
                          if a in question_low), None)
    if subject_alias:
        subject_hits, _ = _fts_search(conn, subject_alias, 40, blocks=blocks,
                                      include_demo=include_demo)

    # --- решения о доверии каналам -------------------------------
    w_fts = 1.0 if fts else 0.0
    trusted_vec = (vec_backend.startswith("ollama")
                   and max_cos >= config.MIN_COSINE)
    w_vec = 1.0 if (vec and trusted_vec) else 0.0

    stats = {
        "fts": len(fts), "vector": len(vec), "dim": None,
        "terms": n_terms, "empty_index": False,
        "vector_backend": vec_backend,
        "vector_max_cos": round(max_cos, 4),
        "vector_trusted": bool(trusted_vec),
        "vector_dropped_reason": (
            "" if trusted_vec else
            ("векторы в аварийном режиме (" + vec_backend + ")" if vec
             and vec_backend == "hash-fallback" else
             "ни один фрагмент не близок к вопросу" if vec
             else "векторы не рассчитаны")),
        "fusion": "fts-only" if w_vec == 0 else "hybrid",
        "blocks_filter": list(blocks) if blocks else None,
    }

    if not fts and not vec:
        stats["empty_index"] = not any(
            conn.execute("SELECT COUNT(*) FROM chunk_vec").fetchone())
        if blocks:
            # Фильтр ничего не дал — это не «индекс пуст». Говорить
            # «ответ не найден» здесь нельзя: по корпусу в целом
            # документы есть, просто не про этот домен.
            stats["filtered_out"] = True
        return [], stats

    # Обрезаем оба канала до одинаковой длины ДО слияния
    fts = fts[:pool]
    vec = vec[:pool] if w_vec else []

    ranks = {}
    for pos, (cid, score) in enumerate(fts):
        ranks.setdefault(cid, {})["fts"] = (pos + 1, score)
    for pos, (cid, score) in enumerate(vec):
        ranks.setdefault(cid, {})["vec"] = (pos + 1, score)
    for pos, (cid, score) in enumerate(subject_hits):
        ranks.setdefault(cid, {})["subj"] = (pos + 1, score)

    merged = []
    for cid, parts in ranks.items():
        rrf = 0.0
        for key, weight in (("fts", w_fts), ("vec", w_vec),
                            ("subj", 1.0 if subject_hits else 0.0)):
            if key in parts and weight:
                rrf += weight / (config.RRF_K + parts[key][0])
        if rrf <= 0:
            continue
        merged.append((cid, rrf, parts))

    merged.sort(key=lambda x: -x[1])
    # НЕ обрезаем здесь до top_k. Надбавки за точное название и год
    # применяются ниже, и обрезка до них лишала кандидата из глубины
    # шанса подняться: строка ИУБиП с рангом 90 в FTS не могла попасть
    # в ответ, хотя после переранжирования была второй.

    if not merged:
        return [], stats

    ids = [m[0] for m in merged]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT ch.chunk_id, ch.doc_id, ch.heading, ch.text,"
        f" d.title AS doc_title, d.rel_path, d.doc_type, d.is_demo"
        f" FROM doc_chunk ch JOIN doc d ON d.doc_id = ch.doc_id"
        f" WHERE ch.chunk_id IN ({placeholders})", ids).fetchall()
    by_id = {int(r["chunk_id"]): r for r in rows}

    # Переранжирование по покрытию. RRF умеет только ранжировать
    # каналы, но не понимает, что совпадение в названии документа
    # важнее совпадения в середине абзаца: на вопрос про аттестацию
    # регламент об отчислениях выигрывал за счёт слов «аттестация» и
    # «преподаватель», тогда как нужный документ содержит только
    # первое — но зато в самом названии.
    stems = query_stems(question)
    quoted = [_norm_field(s) for s in _QUOTED_RE.findall(question)]
    year_match = _YEAR_RE.search(question)
    year = year_match.group(1) if year_match else None
    question_low = question.lower()
    subject = any(alias in question_low for alias in config.SUBJECT_ALIASES)
    scored = []
    for cid, rrf, parts in merged:
        r = by_id.get(cid)
        if not r:
            continue
        cov = weighted_coverage(stems, r["doc_title"], r["heading"], r["text"])
        field = _norm_field(" ".join((r["doc_title"] or "", r["heading"] or "",
                                      r["text"] or "")))
        bonus = 0.0
        # Название/раздел в кавычках совпал дословно — это тот самый вуз
        # или тот самый показатель, а не «похожий по префиксу».
        for span in quoted:
            if span and span in field:
                bonus += config.QUOTED_BONUS
        # Вопрос про конкретный выпуск — не подмешиваем другой год.
        if year and year in (r["doc_title"] or ""):
            bonus += config.YEAR_BONUS
        if subject and "иубип" in field:
            bonus += config.SUBJECT_BONUS
        scored.append((cid, rrf + config.COVERAGE_WEIGHT * cov + bonus,
                       cov, parts, r))

    scored.sort(key=lambda x: -x[1])
    results = []
    per_doc = {}
    for cid, score, cov, parts, r in scored:
        if len(results) >= top_k:
            break
        doc_id = r["doc_id"]
        if per_doc.get(doc_id, 0) >= config.CONTEXT_MAX_PER_DOC:
            continue
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        results.append({
            "chunk_id": cid,
            "doc_id": r["doc_id"],
            "doc_title": r["doc_title"],
            "rel_path": r["rel_path"],
            "doc_type": r["doc_type"],
            "is_demo": bool(r["is_demo"]),
            "heading": r["heading"],
            "text": r["text"],
            "score": round(score, 6),
            "coverage": round(cov, 3),
            "fts_rank": parts.get("fts", (None, None))[0],
            "vec_rank": parts.get("vec", (None, None))[0],
            "found_by": "+".join(k for k in ("fts", "vec") if k in parts),
        })
    return results, stats