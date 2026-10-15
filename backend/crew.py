# -*- coding: utf-8 -*-
"""
Оркестрация агентов.

Порядок работы и почему именно такой:

    вопрос → роутер → специалист домена → критик → (одна правка)

Роутер не отвечает, специалист отвечает, критик проверяет. Три вызова
модели на вопрос — на слабом железе это минута, поэтому есть режимы:

    fast  — роутер + специалист, без критика
    crew  — полный цикл (по умолчанию)

Правка по замечанию критика делается ОДИН раз. Если разрешить
зацикливание, система на слабой модели будет крутиться до достижения
сходящегося состояния — то есть до конца таймаута.

Отдельно про отказы. Если модель недоступна, экипаж не притворяется
работающим: он возвращает одиночный ответ и честно перечисляет, какие
агенты не отработали и почему. Иначе в интерфейсе будет написано
«проверено критиком», а проверки не было.
"""
import re
import time

from . import config
from . import db
from . import llm
from . import retrieve
from .agents import critic as critic_mod
from .agents import registry

MODES = ("fast", "crew")


def _route(question, model=None):
    """Определяет домен. Возвращает (agent_id, qtype, reason)."""
    hint = "\n".join(f"{k} — {v}" for k, v in registry.block_titles().items())
    user = ("ВОПРОС: " + question + "\n\nДомены:\n" + hint)
    text, meta = llm.chat(registry.ROUTER.system, user, model=model,
                          temperature=0.0, num_predict=120)
    if not meta.get("ok"):
        return None, None, meta.get("reason")

    agent_id, qtype = None, None
    for line in text.splitlines():
        up = line.strip().upper()
        if up.startswith("ДОМЕН") and ":" in line:
            val = line.split(":", 1)[1].strip().lower()
            if val in registry.SPECIALISTS_BY_ID or val == "general":
                agent_id = val
        elif up.startswith("ТИП") and ":" in line:
            qtype = line.split(":", 1)[1].strip().lower()
    if agent_id is None:
        # Роутер ответил не по формату. Угадывать домен по тексту ответа
        # опасно: «сводка по всем блокам» была бы отнесена к первому
        # встреченному слову. Честный запасной путь — общий специалист.
        agent_id = "general"
        return agent_id, qtype, "роутер ответил не по формату, взят сводный аналитик"
    return agent_id, qtype, None


def _specialist_answer(agent, question, qtype, conn, fix_note=None):
    """Поиск в блоках агента + ответ. Возвращает (текст, фрагменты, статистика, meta)."""
    top_k = max(config.RETRIEVE_TOP_K, config.CONTEXT_CHUNKS * 2)
    chunks, stats = retrieve.search(conn, question, top_k=top_k,
                                    blocks=agent.blocks or None)
    if not chunks:
        stats = dict(stats or {})
        stats["empty_index"] = stats.get("empty_index", False)
        return None, [], stats, {"ok": False, "reason": "по этому домену документов нет"}

    context = llm.build_context(chunks)
    user = ("ДОКУМЕНТЫ-ФРАГМЕНТЫ:\n\n" + context + "\n\n---\nВОПРОС: " + question)
    if fix_note:
        # Правка делается не «с нуля»: к тексту ответа прикладывается
        # замечание, а исходный контекст документов остаётся тем же.
        # Иначе модель на 1.5b может «исправить» ответ, придумав новое.
        user = ("ДОКУМЕНТЫ-ФРАГМЕНТЫ:\n\n" + context +
                "\n\n---\nТВОЙ ПРЕДЫДУЩИЙ ОТВЕТ:\n\n" + (fix_note["answer"] or "") +
                "\n\nЗАМЕЧАНИЕ КРИТИКА: " + fix_note["problem"] +
                "\n\nПерепиши ответ с учётом замечания, не выходя за фрагменты.\n"
                "\n---\nВОПРОС: " + question)

    text, meta = llm.chat(agent.system, user, model=None,
                          temperature=agent.temperature,
                          num_predict=agent.num_predict)
    if not meta.get("ok"):
        return None, chunks, stats, meta
    return text.strip(), chunks, stats, meta


def run(question, conn, mode="crew", model=None, top_k=0):
    """Прогоняет экипаж. Всегда возвращает структуру, даже если модель молчит."""
    if mode not in MODES:
        mode = "crew"
    started = time.time()
    trace = []
    notes = []

    def step(agent_id, name, what, ok, extra=None, ms=None):
        entry = {"agent": agent_id, "name": name, "step": what,
                 "ok": bool(ok), "ms": ms}
        if extra:
            entry.update(extra)
        trace.append(entry)
        return entry

    # --- 1. Роутер ----------------------------------------------------
    t0 = time.time()
    agent_id, qtype, route_note = _route(question, model=model)
    ms = int((time.time() - t0) * 1000)
    if agent_id is None:
        step("router", registry.ROUTER.name, "не смог определить домен", False,
             {"reason": route_note}, ms)
        notes.append("Роутер не отработал: " + str(route_note))
        agent_id = "general"
    else:
        step("router", registry.ROUTER.name, "домен: " + agent_id, True,
             {"qtype": qtype, "note": route_note}, ms)

    agent = registry.get(agent_id)

    # --- 2. Специалист ----------------------------------------------
    t0 = time.time()
    answer, chunks, stats, meta = _specialist_answer(agent, question, qtype, conn)
    ms = int((time.time() - t0) * 1000)

    if answer is None:
        reason = meta.get("reason") or "нет ответа"
        step(agent.agent_id, agent.name, "не смог ответить", False,
             {"reason": reason, "search": stats}, ms)
        notes.append(f"{agent.name}: {reason}")
        # Модель недоступна целиком — дальше идти незачем, но и врать
        # нельзя. Отдаём то, что нашли, и говорим про отказ.
        fallback_text, fb_meta = llm.answer(question, chunks)
        return {
            "answer": fallback_text,
            "sources": _sources(chunks),
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "qtype": qtype,
            "mode": mode,
            "trace": trace,
            "notes": notes,
            "critic": {"ok": True, "ran": False,
                       "reason": "не запускался: специалист не ответил"},
            "degraded": True,
            "llm": bool(fb_meta.get("llm")),
            "search": stats,
            "latency_ms": int((time.time() - started) * 1000),
        }

    step(agent.agent_id, agent.name, "ответил по фрагментам", True,
         {"search": stats, "chunks": len(chunks)}, ms)

    # --- 3. Критик ----------------------------------------------------
    verdict = None
    if mode == "crew":
        t0 = time.time()
        verdict = critic_mod.review(answer, chunks, model=model)
        ms = int((time.time() - t0) * 1000)
        if not verdict.ran:
            step("critic", "Критик", "проверку пропустил", False,
                 {"reason": verdict.reason}, ms)
            notes.append("Критик не отработал: " + verdict.reason)
        elif verdict.ok:
            step("critic", "Критик", "ответ подтверждён фрагментами", True,
                 None, ms)
        else:
            step("critic", "Критик", "нашёл неподтверждённое", False,
                 {"problem": verdict.problem, "fix": verdict.fix}, ms)

            # --- 4. Одна правка --------------------------------------
            t0 = time.time()
            fixed, chunks2, stats2, meta2 = _specialist_answer(
                agent, question, qtype, conn,
                fix_note={"answer": answer, "problem":
                          (verdict.problem or "") + " " + (verdict.fix or "")})
            ms = int((time.time() - t0) * 1000)
            if fixed:
                answer = fixed
                chunks = chunks2 or chunks
                stats = stats2 or stats
                step(agent.agent_id, agent.name,
                     "исправил ответ по замечанию", True, None, ms)
            else:
                step(agent.agent_id, agent.name,
                     "не смог исправить, оставлен исходный ответ", False,
                     {"reason": meta2.get("reason")}, ms)
                notes.append(f"{agent.name}: не смог исправить ответ "
                             f"({meta2.get('reason')})")
    else:
        notes.append("Критик не запускался: режим «быстро»")

    return {
        "answer": answer,
        "sources": _sources(chunks),
        "agent_id": agent.agent_id,
        "agent_name": agent.name,
        "qtype": qtype,
        "mode": mode,
        "trace": trace,
        "notes": notes,
        "critic": (verdict.as_dict() if verdict
                   else {"ok": True, "ran": False, "reason": "режим «быстро»"}),
        "degraded": False,
        "llm": True,
        "search": stats,
        "latency_ms": int((time.time() - started) * 1000),
    }


def _sources(chunks, limit=None):
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


def describe():
    """Список агентов для интерфейса и проверки настроек."""
    items = []
    for a in registry.all_agents():
        items.append({
            "id": a.agent_id, "name": a.name, "role": a.role,
            "blocks": a.blocks, "mandate": a.mandate,
            "documents": None,
        })
    return {"agents": items, "modes": list(MODES)}