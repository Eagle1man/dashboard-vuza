# -*- coding: utf-8 -*-
"""
Оценка найденного до передачи модели.

Зачем. Базовый RAG берёт несколько лучших фрагментов и генерирует по
ним ответ как бы то ни было. Если фрагменты нерелевантны, модель всё
равно пишет уверенный текст — и это худший вид отказа, потому что
снаружи он неотличим от правильного ответа.

Поэтому сначала оцениваем контекст и только потом решаем, идти ли в
модель. Три решения:

- ok          — контекст годится, можно отвечать;
- reformulate — контекст слабый, но вопрос разумный: стоит один раз
                переформулировать запрос и поискать шире;
- give_up     — даже переформулировка вряд ли поможет. Лучше честно
                сказать «в документах не найдено» и показать, что
                нашлось, чем подсунуть нерелевантное.

Оценка считается из сигналов, которые уже есть в поиске: покрытие
вопроса фрагментом, ранг в словарном канале, косинус вектора,
число разных документов. Ничего дополнительно не запрашивается у
модели — это принципиально: иначе каждый вопрос станет ещё на один
вызов модели, а на слабой машине это минуты.
"""
from . import config
from . import retrieve

OK = "ok"
REFORMULATE = "reformulate"
GIVE_UP = "give_up"

VERDICT_TEXT = {
    OK: "контекст достаточен",
    REFORMULATE: "контекст слабый, нужен повторный поиск",
    GIVE_UP: "контекста нет и повторный поиск вряд ли поможет",
}


def assess(question: str, chunks, stats=None):
    """Возвращает (решение, объяснение, метрики)."""
    stats = stats or {}
    if not chunks:
        why = stats.get("vector_dropped_reason") or "ничего не найдено"
        return (GIVE_UP,
                "поиск не дал результата (" + str(why) + ")",
                {"n_chunks": 0})

    coverages = [c.get("coverage") or 0.0 for c in chunks]
    top_cov = max(coverages) if coverages else 0.0
    mean_cov = sum(coverages) / len(coverages)
    docs = len({c["doc_id"] for c in chunks})
    has_fts = any(c.get("fts_rank") for c in chunks)
    cos = stats.get("vector_max_cos") or 0.0
    trusted_vec = bool(stats.get("vector_trusted"))

    metrics = {
        "n_chunks": len(chunks),
        "n_docs": docs,
        "coverage_top": round(top_cov, 3),
        "coverage_mean": round(mean_cov, 3),
        "fts_hit": has_fts,
        "vector_cos": cos,
        "vector_trusted": trusted_vec,
    }

    # Векторный канал, когда он доверен, — самый дорогой и самый
    # точный сигнал: близость по смыслу важнее совпадения слов.
    if trusted_vec:
        base = cos
        detail = f"косинус {cos:.2f}"
    else:
        base = mean_cov
        detail = f"покрытие {mean_cov:.2f}"

    if docs == 1 and len(chunks) > 6:
        # Всё найденное из одного документа: скорее всего, поиск просто
        # продолжает выдавать соседние куски одного файла, а нужный
        # документ не нашёлся. Снижаем оценку.
        #
        # Важно, что штраф попадает ИМЕННО В МЕТРИКИ, а не только в
        # решение. Иначе решение и то, чем экипаж сравнивает две попытки
        # поиска между собой, считаются от разных чисел — и сравнение
        # может выбрать худший вариант.
        base *= config.JUDGE_NARROW_PENALTY

    metrics["score"] = round(base, 4)

    if base >= config.JUDGE_OK:
        verdict = OK
        why = detail + " — " + VERDICT_TEXT[OK]
    elif base >= config.JUDGE_LOW:
        verdict = REFORMULATE
        why = (detail + f" при пороге {config.JUDGE_OK:.2f}; "
               + VERDICT_TEXT[REFORMULATE])
    else:
        verdict = GIVE_UP
        why = (detail + f" при пороге {config.JUDGE_LOW:.2f}; "
               + VERDICT_TEXT[GIVE_UP])
    return verdict, why, metrics


def reformulate(question: str):
    """Переформулирует запрос для повторного поиска.

    Из вопроса берутся значимые основы слов и добавляются синонимы из
    справочника метрик. Это не переписывание вопроса под естественный
    язык, а расширение словаря поиска: у документа «отсев», а в
    вопросе «отчисления», и без синонима они не встретятся.
    """
    stems = retrieve.query_stems(question)
    if not stems:
        return question, []
    # Берём основы, а не исходные слова: в индексе лежит слово из
    # документа, а совпадение проверяется префиксом.
    terms = []
    seen = set()
    for s in stems:
        if s not in seen:
            seen.add(s)
            terms.append(s)
    return " ".join(terms), terms