# -*- coding: utf-8 -*-
"""
Проверки экипажа, которые не требуют языковой модели.

Проверяется то, что при отказе модели или нестандартном ответе не
приводит к тихой неправде: разбор вердикта критика и запасной путь
роутера. Остальное без модели проверить нельзя, для этого есть ручной
прогон через /api/ask/crew.

Запуск: python -m backend.tests.test_crew
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.agents import critic as critic_mod  # noqa: E402
from backend.agents import registry  # noqa: E402
from backend import crew  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ОК     {name}")
    else:
        print(f"  ПРОВАЛ {name}  {detail}")
        FAILURES.append(name)


def test_critic_verdict_ok():
    v = critic_mod._parse("ВЕРДИКТ: ОК\nПРОБЛЕМА: -\nИСПРАВЛЕНИЕ: -")
    check("вердикт ОК распознаётся", v.ok and not v.problem, f"ok={v.ok} problem={v.problem!r}")


def test_critic_verdict_fail():
    v = critic_mod._parse(
        "ВЕРДИКТ: НУЖНО ИСПРАВИТЬ\n"
        "ПРОБЛЕМА: в ответе 612 человек, в источнике 68\n"
        "ИСПРАВЛЕНИЕ: сверить цифру с пунктом 3.1")
    check("вердикт на замечание распознаётся", (not v.ok) and "612" in v.problem,
          f"ok={v.ok} problem={v.problem!r}")
    check("исправление извлекается", "пунктом 3.1" in v.fix, f"fix={v.fix!r}")
    check("прочерк в поле не становится замечанием", v.problem != "-", f"problem={v.problem!r}")


def test_critic_garbage_is_not_ok():
    """Мусор в ответе не должен превращаться в «всё хорошо».

    Если считать неразобранный ответ подтверждением, критика можно
    обойти, просто ответив не по формату, — и система будет врать
    ровно там, где нужна проверка.
    """
    v = critic_mod._parse("Ну вроде нормально, ничего подозрительного")
    check("неразобранный вердикт не равен «ОК» без оговорки",
          v.ok and v.reason, "должен быть помечен как неизвестный результат")


def test_critic_empty():
    v = critic_mod._parse("")
    check("пустой ответ помечается как неизвестный", bool(v.reason))


def test_registry_blocks():
    ids = {a.agent_id for a in registry.all_agents()}
    check("девять агентов объявлено", len(ids) == 9, f"найдено {len(ids)}")
    for block_id in ("academic", "results", "finance", "employ",
                     "science", "ops", "reputation"):
        agents = [a for a in registry.all_agents() if block_id in a.blocks]
        check(f"специалист есть для блока {block_id}", len(agents) == 1)


def test_specialists_have_distinct_prompts():
    prompts = [a.system for a in registry.SPECIALISTS]
    check("промпты специалистов различаются", len(set(prompts)) == len(prompts))
    for a in registry.SPECIALISTS:
        check(f"у агента {a.agent_id} есть постановка",
              "ТОЛЬКО по фрагментам" in a.system and a.mandate != "",
              "потеряно базовое правило или mandate")


def test_generalist_has_no_filter():
    g = registry.get("general")
    check("сводный аналитик работает по всему корпусу", g.blocks == [])


def test_unknown_agent_is_generalist():
    check("неизвестный агент не падает", registry.get("такого-нет").agent_id == "general")


def test_degraded_when_model_down():
    """Экипаж без модели обязан честно сказать об этом, а не отвечать ничем."""
    import backend.embed as embed
    import backend.llm as llm

    saved_avail = embed.ollama_available
    saved_chat = llm.chat
    saved_answer = llm.answer
    try:
        embed.ollama_available = lambda: False
        llm.chat = lambda *a, **k: ("", {"ok": False, "reason": "Ollama недоступна (тест)"})
        llm.answer = lambda q, c: ("запасной ответ", {"llm": False, "reason": "нет модели"})

        from backend import db
        conn = db.connect()
        out = crew.run("какая рентабельность", conn, mode="crew")
    finally:
        embed.ollama_available = saved_avail
        llm.chat = saved_chat
        llm.answer = saved_answer
        try:
            conn.close()
        except Exception:
            pass

    check("экипаж сообщает о сбое", out["degraded"] is True, str(out)[:80])
    check("экипаж не притворяется, что ответил LLM", out["llm"] is False)
    check("критик помечен как не запущенный", out["critic"]["ran"] is False)
    check("в следе есть неуспешный шаг",
          any(not s["ok"] for s in out["trace"]), "все шаги успешны")
    check("причина сбоя видна в notes", bool(out["notes"]), str(out["notes"]))


def test_agent_without_documents_fails_honestly():
    """Специалист, которому не досталось документов, должен это сказать.

    Агент ops сейчас видит ноль документов. Если он вместо честного
    отказа начнёт отвечать по чужой теме (по всему корпусу), ректор
    получит уверенный ответ не по тому вопросу — худший исход из
    возможных, потому что он выглядит правдой.
    """
    import backend.embed as embed
    import backend.llm as llm

    saved_chat = llm.chat
    try:
        # Модель «работает», но специалисту нечего показать.
        llm.chat = lambda *a, **k: (" какой-то ответ", {"ok": True})
        from backend import db
        conn = db.connect()
        agent = registry.get("ops")
        answer, chunks, stats, meta = crew._specialist_answer(
            agent, "какая нагрузка у преподавателей", "fact", conn)
    finally:
        llm.chat = saved_chat
        try:
            conn.close()
        except Exception:
            pass

    check("агент без документов не выдумывает ответ", answer is None,
          f"вернул: {str(answer)[:60]!r}")
    check("причина отказа названа", "документ" in str(meta.get("reason", "")),
          str(meta))
    check("в поиске помечено, что это фильтр, а не пустой корпус",
          stats.get("filtered_out") is True or not stats.get("empty_index"),
          str(stats))


def main():
    print("Проверка экипажа агентов (без языковой модели)")
    for fn in (test_critic_verdict_ok, test_critic_verdict_fail,
               test_critic_garbage_is_not_ok, test_critic_empty,
               test_registry_blocks, test_specialists_have_distinct_prompts,
               test_generalist_has_no_filter, test_unknown_agent_is_generalist,
               test_degraded_when_model_down,
               test_agent_without_documents_fails_honestly):
        fn()
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
        return 1
    print("Все проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())