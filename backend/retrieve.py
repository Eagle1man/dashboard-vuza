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


def _fts_search(conn, query: str, limit: int):
    expr = fts_expression(query)
    if not expr:
        return [], 0
    try:
        rows = conn.execute(
            "SELECT c.chunk_id, bm25(chunk_fts) AS score"
            " FROM chunk_fts c JOIN doc_chunk dc ON dc.chunk_id = c.chunk_id"
            " WHERE chunk_fts MATCH ? ORDER BY score LIMIT ?",
            (expr, limit)).fetchall()
    except Exception:
        # FTS5 не должен ронять поиск целиком: ловим и идём только по вектору
        return [], 0
    return [(int(r["chunk_id"]), float(r["score"])) for r in rows], len(expr.split(" OR "))


def _vector_search(conn, question: str, limit: int):
    """Возвращает (список (chunk_id, cos), backend, max_cos)."""
    # Если модели нет, векторный канал всё равно будет отброшен при
    # слиянии — незачем грузить в память все векторы и считать
    # произведение впустую на каждом вопросе.
    if not embed.ollama_available():
        return [], "ollama-offline", 0.0

    rows = conn.execute(
        "SELECT chunk_id, dim, vec FROM chunk_vec").fetchall()
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


def search(conn, question: str, top_k: int = None, pool: int = None):
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
    pool = pool or max(top_k * 3, 30)

    fts, n_terms = _fts_search(conn, question, pool)
    vec, vec_backend, max_cos = _vector_search(conn, question, pool)

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
    }

    if not fts and not vec:
        stats["empty_index"] = not any(
            conn.execute("SELECT COUNT(*) FROM chunk_vec").fetchone())
        return [], stats

    # Обрезаем оба канала до одинаковой длины ДО слияния
    fts = fts[:pool]
    vec = vec[:pool] if w_vec else []

    ranks = {}
    for pos, (cid, score) in enumerate(fts):
        ranks.setdefault(cid, {})["fts"] = (pos + 1, score)
    for pos, (cid, score) in enumerate(vec):
        ranks.setdefault(cid, {})["vec"] = (pos + 1, score)

    merged = []
    for cid, parts in ranks.items():
        rrf = 0.0
        for key, weight in (("fts", w_fts), ("vec", w_vec)):
            if key in parts and weight:
                rrf += weight / (config.RRF_K + parts[key][0])
        if rrf <= 0:
            continue
        merged.append((cid, rrf, parts))

    merged.sort(key=lambda x: -x[1])
    merged = merged[:top_k]

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
    scored = []
    for cid, rrf, parts in merged:
        r = by_id.get(cid)
        if not r:
            continue
        cov = weighted_coverage(stems, r["doc_title"], r["heading"], r["text"])
        scored.append((cid, rrf + config.COVERAGE_WEIGHT * cov, cov, parts, r))

    scored.sort(key=lambda x: -x[1])
    results = []
    for cid, score, cov, parts, r in scored:
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