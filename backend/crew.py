# -*- coding: utf-8 -*-
"""
Оркестрация агентов.

Порядок работы и почему именно такой:

    вопрос → роутер → [числа из витрины] → [поиск + оценка контекста]
           → специалист → критик → (одна правка)

Роутер не отвечает, а выбирает домен и режим: брать цифры из витрины,
отвечать по документам или и то и другое. Дальше есть три развилки.

1. ЧИСЛА. Если режим про витрину, считаем по fact_metric. Пометка
   «демонстрационные» ставится кодом, а не просьбой модели: если
   положиться только на инструкцию, то при неудачном запросе метка
   потеряется и ответ будет выглядеть как отчётные данные.

2. ОЦЕНКА КОНТЕКСТА до генерации. Если найденное слабое, вопрос
   переформулируется один раз и поиск идёт шире. Если и после этого
   пусто, система честно отказывается отвечать. Это корректирующий
   RAG: он не даёт модели писать уверенный текст по нерелевантному.

3. КРИТИК после генерации. Отдельный вызов модели со своим контекстом,
   плюс не больше одной попытки правки.

Про отказы. Экипаж не притворяется, что отработал: любой отказ
попадает в след с признаком сбоя и причиной, и это видно в интерфейсе.
"""
import re
import time

from . import config
from . import db
from . import llm
from . import retrieval_judge
from . import retrieve
from . import tools
from .agents import critic as critic_mod
from .agents import registry

MODES = ("fast", "crew")
RESP_MODES = ("documents", "metrics", "both")

# Вопросы, где почти наверняка нужны числа, а не цитаты.
#
# Наблюдение из живого прогона: на вопрос «покажи отчисления по
# факультетам» роутер на модели 1.5b выбрал режим документов, и агент
# честно ответил, что в документах таких данных нет — они есть только
# в витрине. Формально он был прав, ректору это не помогло.
#
# Поэтому решение роутера здесь считается подсказкой: если вопрос
# просит разбивку или динамику и метрика в витрине находится, числа
# добавляются в любом случае. Ошибиться можно только в сторону
# «показать больше», а не в сторону «промолчать».
METRICS_HINT = re.compile(
    r"по\s+(?:факультет|подраздел|институт|кафедр)"
    r"|разбивк|распределени"
    r"|динамик|изменени|как\s+измен"
    r"|за\s+(?:год|квартал|месяц|период)"
    r"|сравн|больше\s+всех|меньше\s+всех"
    r"|топ|рейтинг",
    re.I)

NOT_FOUND = (
    "В загруженных документах ответ на этот вопрос не найден, и "
    "повторный поиск его не дал.\n\n"
    "Что нашлось рядом (может быть полезно, может быть нет):\n"
    "{found}\n\n"
    "Что можно сделать: добавить нужный документ в папку knowledge "
    "и запустить start.bat заново.")


def _route(question, model=None):
    """Определяет домен, тип и режим. (domain, qtype, resp_mode, note)."""
    hint = "\n".join(f"{k} — {v}" for k, v in registry.block_titles().items())
    user = "ВОПРОС: " + question + "\n\nДомены:\n" + hint
    text, meta = llm.chat(registry.ROUTER.system, user, model=model,
                          temperature=0.0, num_predict=140)
    if not meta.get("ok"):
        return None, None, None, meta.get("reason")

    agent_id, qtype, resp_mode = None, None, None
    for line in text.splitlines():
        up = line.strip().upper()
        if up.startswith("ДОМЕН") and ":" in line:
            val = line.split(":", 1)[1].strip().lower()
            if val in registry.SPECIALISTS_BY_ID or val in ("general", "analyst"):
                agent_id = val
        elif up.startswith("ТИП") and ":" in line:
            qtype = line.split(":", 1)[1].strip().lower()
        elif up.startswith("РЕЖИМ") and ":" in line:
            val = line.split(":", 1)[1].strip().lower()
            if val in RESP_MODES:
                resp_mode = val

    note = []
    if agent_id is None:
        # Угадывать домен по тексту ответа опасно: «сводка по всем
        # блокам» ушла бы к первому попавшемуся слову.
        agent_id, note = "general", ["роутер не назвал домен, взят сводный аналитик"]
    if resp_mode is None:
        resp_mode = "both" if agent_id != "general" else "documents"
        note.append("роутер не назвал режим, взят " + resp_mode)
    if agent_id == "analyst":
        # Аналитик витрины — не домен, а роль. Домен остаётся общим,
        # иначе вопрос «сколько отсева на Экономическом факультете»
        # потерял бы фильтр по домену.
        resp_mode = "metrics" if resp_mode == "documents" else resp_mode
    return agent_id, qtype, resp_mode, ("; ".join(note) or None)


def _graph_rescue(question, chunks, conn, steps, limit=18):
    """Пробует довести ответ через связи между документами.

    Нужно там, где поиск по словам бессилен: вопрос «что решил
    протокол № 14» или «что изменило положение о приёме» не
    обязательно содержит слова нужного документа. Обход графа идёт
    от номера или названия, названных в вопросе, и приносит соседние
    документы.

    Возвращает (добавленные фрагменты, описание). Уже найденное
    повторно не добавляется.
    """
    try:
        import graph
    except ImportError:
        return [], {"reason": "модуль графа недоступен"}

    t0 = time.time()
    try:
        g_chunks, info = graph.expand(conn, question, hops=2)
    except Exception as exc:
        steps.append({"agent": "graph", "name": "Граф ссылок", "ok": False,
                      "ms": int((time.time() - t0) * 1000),
                      "step": "обход не удался", "reason": str(exc)[:120]})
        return [], {}
    ms = int((time.time() - t0) * 1000)

    if not g_chunks:
        steps.append({"agent": "graph", "name": "Граф ссылок", "ok": False,
                      "ms": ms, "step": "связей из вопроса не нашлось",
                      "reason": info.get("reason") or "неизвестно"})
        return [], info

    have = {c["chunk_id"] for c in chunks}
    fresh = [c for c in g_chunks if c["chunk_id"] not in have][:limit]
    if not fresh:
        steps.append({"agent": "graph", "name": "Граф ссылок", "ok": True,
                      "ms": ms,
                      "step": "документы по связям уже были в выдаче"})
        return [], info

    docs = sorted({c["doc_id"] for c in fresh})
    steps.append({
        "agent": "graph", "name": "Граф ссылок", "ok": True, "ms": ms,
        "step": f"по связям добавлено фрагментов: {len(fresh)} из документов {len(docs)}",
        "seed_labels": info.get("seed_labels") or [],
        "graph_docs": docs,
    })
    return fresh, info


def _collect_docs(question, agent, conn, steps, top_k):
    """Поиск, оценка контекста, переформулировка и граф — по порядку."""
    top_k = top_k or max(config.RETRIEVE_TOP_K, config.CONTEXT_CHUNKS * 2)
    blocks = agent.blocks or None

    chunks, stats = retrieve.search(conn, question, top_k=top_k, blocks=blocks)
    verdict, why, metrics = retrieval_judge.assess(question, chunks, stats)
    steps.append({
        "agent": "judge", "name": "Оценка контекста",
        "ok": verdict != retrieval_judge.GIVE_UP,
        "step": "найдено фрагментов: " + str(metrics["n_chunks"]) + " — " + why,
        "ms": 0, "judge": verdict, "judge_metrics": metrics,
    })

    if verdict == retrieval_judge.REFORMULATE:
        # Переформулировка: расширяем словарь основами слов и ищем без
        # доменного фильтра — вдруг вопрос ушёл не туда.
        q2, _terms = retrieval_judge.reformulate(question)
        t0 = time.time()
        chunks2, stats2 = retrieve.search(conn, q2, top_k=top_k, blocks=None)
        v2, why2, m2 = retrieval_judge.assess(q2, chunks2, stats2)
        ms = int((time.time() - t0) * 1000)
        better = len(chunks2) > 0 and _score(m2) > _score(metrics)
        steps.append({
            "agent": "judge", "name": "Оценка контекста",
            "ok": v2 != retrieval_judge.GIVE_UP, "ms": ms,
            "step": ("повторный поиск шире: " + why2) if better
                    else ("повторный поиск не улучшил: " + why2),
            "judge": v2, "judge_metrics": m2,
        })
        if better:
            chunks, stats, verdict, why = chunks2, stats2, v2, why2

    # Граф пробуем, когда контекст ещё слабый. На хорошем контексте
    # обход только разбавит его соседями и замедлит ответ.
    if verdict != retrieval_judge.OK:
        fresh, info = _graph_rescue(question, chunks, conn, steps)
        if fresh:
            merged = list(chunks) + fresh
            v3, why3, m3 = retrieval_judge.assess(question, merged, stats)
            steps.append({
                "agent": "judge", "name": "Оценка контекста",
                "ok": v3 != retrieval_judge.GIVE_UP, "ms": 0,
                "step": "после добавления по связям: " + why3,
                "judge": v3, "judge_metrics": m3,
            })
            chunks = merged[:top_k]
            verdict, why = v3, why3

    return chunks, stats, verdict, why


def _score(metrics):
    """Итоговый балл оценки контекста.

    Берётся готовое значение из метрик, а не пересчитывается здесь:
    так сравнение двух попыток поиска использует ровно тот же балл,
    на котором принималось решение, включая штраф за сужение.
    """
    if not metrics:
        return 0.0
    if "score" in metrics:
        return float(metrics["score"])
    if metrics.get("vector_trusted"):
        return float(metrics.get("vector_cos", 0.0))
    return float(metrics.get("coverage_mean", 0.0))


def _generate(agent, question, chunks, metrics_data, fix_note=None):
    """Ответ агента по найденному. Возвращает (текст|None, chunks, meta)."""
    parts = []
    if chunks:
        parts.append("ДОКУМЕНТЫ-ФРАГМЕНТЫ:\n\n" + llm.build_context(chunks))
    if metrics_data and metrics_data.get("ok"):
        parts.append(tools.render(metrics_data))
    if not parts:
        return None, chunks, {"ok": False, "reason": "нечего предъявить модели"}

    user = "\n\n---\n\n".join(parts) + "\n\n---\nВОПРОС: " + question
    if fix_note:
        user = ("\n\n---\n\n".join(parts) + "\n\n---\n"
                "ТВОЙ ПРЕДЫДУЩИЙ ОТВЕТ:\n\n" + (fix_note["answer"] or "") +
                "\n\nЗАМЕЧАНИЕ КРИТИКА: " + fix_note["problem"] +
                "\n\nПерепиши ответ с учётом замечания, не выходя за источники."
                "\n\n---\nВОПРОС: " + question)

    text, meta = llm.chat(agent.system, user, temperature=agent.temperature,
                          num_predict=agent.num_predict)
    return (text.strip() if meta.get("ok") else None), chunks, meta


def run(question, conn, mode="crew", model=None, top_k=0):
    if mode not in MODES:
        mode = "crew"
    started = time.time()
    trace, notes = [], []

    # --- 1. Роутер ----------------------------------------------------
    t0 = time.time()
    agent_id, qtype, resp_mode, route_note = _route(question, model=model)
    ms = int((time.time() - t0) * 1000)
    if agent_id is None:
        trace.append({"agent": "router", "name": "Роутер",
                      "ok": False, "ms": ms,
                      "step": "не смог определить домен",
                      "reason": str(route_note)})
        notes.append("Роутер не отработал: " + str(route_note))
        agent_id, qtype, resp_mode = "general", None, "documents"
    else:
        trace.append({"agent": "router", "name": "Роутер", "ok": True, "ms": ms,
                      "step": "домен: " + agent_id + ", режим: " + resp_mode,
                      "qtype": qtype, "note": route_note})

    # --- 2. Числа из витрины -----------------------------------------
    # Считаем всегда, даже когда роутер выбрал документы: запрос к
    # витрине стоит миллисекунды, а знание о том, что метрика
    # нашлась, само по себе полезно.
    metrics_data = tools.query(question, conn)

    if resp_mode in ("metrics", "both") or (
            resp_mode == "documents" and metrics_data.get("ok")
            and METRICS_HINT.search(question)):
        t0 = time.time()
        ms = int((time.time() - t0) * 1000)
        if metrics_data.get("ok"):
            step_txt = "посчитано: " + metrics_data["metric"]["title"]
            forced = False
            if resp_mode == "documents":
                # Роутер ошибся: вопрос о разбивке или динамике, а он
                # выбрал документы. Это поправка на основе самого
                # вопроса, а не на догадке о модели.
                resp_mode = "both"
                forced = True
                step_txt += " (режим изменён на «числа и документы»: " \
                            "вопрос просит разбивку или динамику)"
                notes.append("Режим уточнён: вопрос про разбивку или динамику, "
                             "добавлены числа из витрины")
            trace.append({"agent": "analyst", "name": "Аналитик витрины",
                          "ok": True, "ms": ms, "step": step_txt,
                          "demo": True, "forced": forced})
        elif resp_mode in ("metrics", "both"):
            trace.append({"agent": "analyst", "name": "Аналитик витрины",
                          "ok": False, "ms": ms,
                          "step": "показатель не распознан",
                          "reason": metrics_data.get("reason")})
        else:
            metrics_data = None
    else:
        metrics_data = None

    # --- 3. Документы и оценка контекста ------------------------------
    agent = registry.get(agent_id)
    chunks, stats, verdict, why = [], {}, retrieval_judge.GIVE_UP, ""

    if resp_mode in ("documents", "both"):
        t0 = time.time()
        chunks, stats, verdict, why = _collect_docs(question, agent, conn, trace, top_k)
        ms = int((time.time() - t0) * 1000)
        if verdict == retrieval_judge.GIVE_UP and not metrics_data:
            trace.append({"agent": agent.agent_id, "name": agent.name,
                          "ok": False, "ms": ms,
                          "step": "не отвечает: контекст не собран",
                          "reason": why})
            notes.append("Контекст не собран, ответ не сформирован")
            return _pack(None, chunks, agent_id, agent, qtype, resp_mode, mode,
                         trace, notes, {"ok": True, "ran": False,
                                        "reason": "не запускался: нечего проверять"},
                         metrics_data, stats, True, started)
    elif resp_mode == "metrics" and metrics_data:
        verdict = retrieval_judge.OK

    # --- 4. Ответ специалиста -----------------------------------------
    gen_agent = registry.get("analyst") if resp_mode == "metrics" else agent
    t0 = time.time()
    answer, chunks2, meta = _generate(gen_agent, question, chunks, metrics_data)
    ms = int((time.time() - t0) * 1000)
    if answer is None:
        trace.append({"agent": gen_agent.agent_id, "name": gen_agent.name,
                      "ok": False, "ms": ms, "step": "не смог ответить",
                      "reason": meta.get("reason")})
        notes.append(f"{gen_agent.name}: {meta.get('reason')}")
        fallback_text, fb_meta = llm.answer(question, chunks)
        return _pack(fallback_text, chunks, agent_id, agent, qtype, resp_mode,
                     mode, trace, notes,
                     {"ok": True, "ran": False,
                      "reason": "не запускался: специалист не ответил"},
                     metrics_data, stats, True, started)

    trace.append({"agent": gen_agent.agent_id, "name": gen_agent.name,
                  "ok": True, "ms": ms,
                  "step": "ответил по источникам", "chunks": len(chunks)})

    # --- 5. Критик ------------------------------------------------------
    verdict_c = None
    if mode == "crew":
        t0 = time.time()
        verdict_c = critic_mod.review(answer, chunks, model=model)
        ms = int((time.time() - t0) * 1000)
        if not verdict_c.ran:
            trace.append({"agent": "critic", "name": "Критик", "ok": False,
                          "ms": ms, "step": "проверку пропустил",
                          "reason": verdict_c.reason})
            notes.append("Критик не отработал: " + verdict_c.reason)
        elif verdict_c.ok:
            trace.append({"agent": "critic", "name": "Критик", "ok": True,
                          "ms": ms, "step": "ответ подтверждён источниками"})
        else:
            trace.append({"agent": "critic", "name": "Критик", "ok": False,
                          "ms": ms, "step": "нашёл неподтверждённое",
                          "problem": verdict_c.problem, "fix": verdict_c.fix})
            t0 = time.time()
            fixed, _ch, meta2 = _generate(
                gen_agent, question, chunks, metrics_data,
                fix_note={"answer": answer,
                          "problem": (verdict_c.problem or "") + " " + (verdict_c.fix or "")})
            ms = int((time.time() - t0) * 1000)
            if fixed:
                answer = fixed
                trace.append({"agent": gen_agent.agent_id, "name": gen_agent.name,
                              "ok": True, "ms": ms,
                              "step": "исправил ответ по замечанию"})
            else:
                trace.append({"agent": gen_agent.agent_id, "name": gen_agent.name,
                              "ok": False, "ms": ms,
                              "step": "не смог исправить, оставлен исходный",
                              "reason": meta2.get("reason")})
                notes.append(f"{gen_agent.name}: не смог исправить ответ")
    else:
        notes.append("Критик не запускался: режим «быстро»")

    return _pack(answer, chunks, agent_id, agent, qtype, resp_mode, mode,
                 trace, notes,
                 (verdict_c.as_dict() if verdict_c
                  else {"ok": True, "ran": False, "reason": "режим «быстро»"}),
                 metrics_data, stats, False, started)


def _pack(answer, chunks, agent_id, agent, qtype, resp_mode, mode, trace,
          notes, verdict, metrics_data, stats, degraded, started):
    """Собирает единый ответ экипажа."""
    found = "\n".join(
        f"- «{c['doc_title']}»" + (f", раздел {c['heading']}" if c.get("heading") else "")
        for c in chunks[:5]
    ) or "(ничего не нашлось)"
    return {
        "answer": answer or NOT_FOUND.format(found=found),
        "sources": _sources(chunks),
        "agent_id": agent_id,
        "agent_name": agent.name,
        "qtype": qtype,
        "resp_mode": resp_mode,
        "mode": mode,
        "trace": trace,
        "notes": notes,
        "critic": verdict,
        "metrics": _metrics_public(metrics_data),
        "degraded": degraded,
        "llm": bool(answer) and not degraded,
        "search": stats or {},
        "latency_ms": int((time.time() - started) * 1000),
    }


def _sources(chunks, limit=None):
    """Источники для показа под ответом."""
    limit = limit or config.CONTEXT_CHUNKS
    return [{
        "n": i + 1,
        "title": ch["doc_title"],
        "heading": ch["heading"],
        "rel_path": ch["rel_path"],
        "doc_type": ch["doc_type"],
        "is_demo": ch["is_demo"],
        "score": ch["score"],
        "coverage": ch.get("coverage"),
        "found_by": ch["found_by"],
        "snippet": " ".join(ch["text"].split())[:400],
    } for i, ch in enumerate(chunks[:limit])]


def _metrics_public(metrics_data):
    """Наружу отдаём без служебных полей, но с пометкой демо-данных."""
    if not metrics_data or not metrics_data.get("ok"):
        return None
    out = dict(metrics_data)
    # Подпись должна совпадать с тем, откуда взято число. Официальный
    # показатель — не демонстрационный, и называть его демо-витриной
    # значит солгать ректору в подписи под ответом.
    out["label"] = ("число рассчитано по демонстрационной витрине"
                    if out.get("demo") else
                    "число взято из документов университета")
    return out


def describe():
    """Состав экипажа для интерфейса: кто есть, за что отвечает,
    какие блоки видит и сколько документов ему достаётся."""
    items = []
    for a in registry.all_agents():
        items.append({
            "id": a.agent_id, "name": a.name, "role": a.role,
            "blocks": a.blocks, "mandate": a.mandate, "documents": None,
        })
    return {"agents": items, "modes": list(MODES),
            "resp_modes": list(RESP_MODES)}