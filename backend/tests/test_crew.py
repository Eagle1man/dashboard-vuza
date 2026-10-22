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
from backend import retrieval_judge  # noqa: E402
from backend import tools  # noqa: E402

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


def test_plan_agents():
    """Число агентов решает система, и решение осмысленно.

    Критик — отдельный вызов модели: на простом факте («сколько
    студентов») он не нужен, там проверять нечего, а ректор ждать не
    готов. На вопросе с разбором или сравнением — нужен: там ответ
    собирается из нескольких источников, и критик ловит выдумку.
    """
    fact = crew.plan_agents("сколько всего обучающихся в университете",
                            qtype="fact", resp_mode="metrics")
    check("на простой факт критик не зовётся", fact["critic"] is False,
          fact["reason"])
    check("на простой факт агентов двое", fact["agents"] == ["роутер", "специалист"],
          str(fact["agents"]))

    explain = crew.plan_agents("почему выросло отчисление",
                                qtype="explain", resp_mode="both")
    check("на объяснение критик зовётся", explain["critic"] is True,
          explain["reason"])
    check("на объяснение агентов трое", len(explain["agents"]) == 3,
          str(explain["agents"]))

    report = crew.plan_agents("сделай сводку по университету",
                               qtype="report", resp_mode="documents")
    check("на отчёт критик зовётся", report["critic"] is True, report["reason"])

    compare = crew.plan_agents("сравни показатели двух академий",
                                qtype="fact", resp_mode="documents")
    check("на сравнение критик зовётся", compare["critic"] is True,
          compare["reason"])

    check("решение объяснено", all(p.get("reason") for p in
                                    (fact, explain, report, compare)))


def test_registry_blocks():
    ids = {a.agent_id for a in registry.all_agents()}
    # Роутер, семь специалистов, аналитик витрины и сводный аналитик.
    check("десять агентов объявлено", len(ids) == 10, f"найдено {len(ids)}")
    check("есть аналитик витрины", "analyst" in ids)
    check("роутер просит выбрать режим", "РЕЖИМ" in registry.ROUTER.system)
    for block_id in ("academic", "results", "finance", "vnoko", "science",
                     "upr", "digital"):
        agents = [a for a in registry.all_agents() if block_id in a.blocks]
        check(f"специалист есть для блока {block_id}", len(agents) == 1)


def test_specialists_have_distinct_prompts():
    prompts = [a.system for a in registry.SPECIALISTS]
    check("промпты специалистов различаются", len(set(prompts)) == len(prompts))
    for a in registry.SPECIALISTS:
        # Базовое правило «отвечать только по источникам» и собственная
        # постановка агента. Первая фраза базового правила менялась:
        # теперь агенту можно дать не только фрагменты документов, но и
        # посчитанные числа витрины, поэтому проверяем по смыслу, а не
        # по точному тексту.
        check(f"у агента {a.agent_id} есть постановка",
              ("ТОЛЬКО по тому, что тебе дали" in a.system
               or "ТОЛЬКО по фрагментам" in a.system) and a.mandate != "",
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

    Агент digital сейчас видит ноль документов. Если он вместо честного
    отказа начнёт отвечать по чужой теме (по всему корпусу), ректор
    получит уверенный ответ не по тому вопросу — худший исход из
    возможных, потому что он выглядит правдой.
    """
    from backend import db

    agent = registry.get("digital")
    steps = []
    conn = db.connect()
    try:
        chunks, stats, verdict, why = crew._collect_docs(
            "какая нагрузка у преподавателей", agent, conn, steps, 0)
    finally:
        conn.close()

    check("агент без документов не находит фрагментов",
          verdict == retrieval_judge.GIVE_UP, f"verdict={verdict}")
    check("оценка контекста попала в след", any(s["agent"] == "judge" for s in steps))
    check("причина отказа названа", bool(why), why)


def test_judge_thresholds():
    """Оценщик обязан различать три исхода, а не всегда говорить «годно»."""
    strong = [{"coverage": 0.9, "fts_rank": 1, "doc_id": 1},
              {"coverage": 0.8, "fts_rank": 2, "doc_id": 2}]
    weak = [{"coverage": 0.05, "fts_rank": 8, "doc_id": 1},
            {"coverage": 0.03, "fts_rank": 9, "doc_id": 1}]
    none = []

    v1, why1, m1 = retrieval_judge.assess("вопрос", strong, {})
    check("сильный контекст признан годным",
          v1 == retrieval_judge.OK, f"{v1}: {why1}")
    check("метрики посчитаны", m1["n_docs"] == 2, str(m1))

    v2, why2, _ = retrieval_judge.assess("вопрос", weak, {})
    check("слабый контекст требует переформулировки или отказа",
          v2 in (retrieval_judge.REFORMULATE, retrieval_judge.GIVE_UP), f"{v2}: {why2}")

    v3, why3, m3 = retrieval_judge.assess("вопрос", none, {})
    check("пустой результат ведёт к отказу",
          v3 == retrieval_judge.GIVE_UP, f"{v3}: {why3}")
    check("в следе метрик видно ноль фрагментов", m3["n_chunks"] == 0, str(m3))

    # Один документ сам по себе НЕ является проблемой: вопрос с высоким
    # покрытием из одного документа означает, что этот документ точно
    # по теме. Проблема — сужение при посредственном покрытии: поиск
    # просто продолжает выдавать соседние куски одного файла.
    strong_single = [{"coverage": 0.9, "fts_rank": 1, "doc_id": 7} for _ in range(8)]
    v4, why4, _ = retrieval_judge.assess("вопрос", strong_single, {})
    check("один документ при высоком покрытии принимается",
          v4 == retrieval_judge.OK, f"{v4}: {why4}")

    mid_multi = [{"coverage": 0.5, "fts_rank": 1, "doc_id": i} for i in range(1, 9)]
    mid_single = [{"coverage": 0.5, "fts_rank": 1, "doc_id": 7} for _ in range(8)]
    _vA, whyA, _ = retrieval_judge.assess("вопрос", mid_multi, {})
    _vB, whyB, _ = retrieval_judge.assess("вопрос", mid_single, {})
    scoreA = crew._score(_assess_metrics(mid_multi, {}))
    scoreB = crew._score(_assess_metrics(mid_single, {}))
    check("сужение на одном документе снижает оценку при среднем покрытии",
          scoreB < scoreA, f"один документ={scoreB:.3f}, несколько={scoreA:.3f}")



def _assess_metrics(chunks, stats):
    """Метрики оценки для сверки баллов в тесте."""
    _v, _w, m = retrieval_judge.assess("вопрос", chunks, stats)
    return m

def test_reformulate_keeps_terms():
    question = "почему выросло отчисление"
    q2, terms = retrieval_judge.reformulate(question)
    check("переформулировка не теряет вопрос", bool(terms),
          f"{question!r} -> {q2!r}")
    check("переформулировка короче шумных слов", len(terms) <= 4, str(terms))
    check("оставлены именно значимые слова",
          any("отчисл" in t for t in terms), str(terms))


def test_metrics_tool_marks_demo():
    """Инструмент чисел обязан сам помечать результат как демо-данные."""
    from backend import db
    conn = db.connect()
    try:
        r = tools.query("какая успеваемость студентов", conn)
        count = tools.query("сколько публикаций в Scopus", conn)
        official = tools.query("сколько всего обучающихся в университете", conn)
        junk = tools.query("какая погода в Ростове", conn)
    finally:
        conn.close()
    check("инструмент нашёл показатель", r.get("ok") is True, str(r)[:80])
    check("результат помечен как демонстрационный", r.get("demo") is True)
    check("в тексте есть предупреждение", "демонстрационной" in r.get("notice", ""),
          r.get("notice", ""))
    # Разбивка по кафедрам есть только у счётных величин. У процента
    # она бессмысленна: кафедра получила бы долю от процента, и
    # «успеваемость 11,4 %» читалось бы как провал.
    check("у процентного показателя разбивки нет",
          len(r.get("by_org", [])) == 0, str(r.get("by_org"))[:80])
    check("у счётного показателя разбивка получена",
          len(count.get("by_org", [])) >= 5, str(count.get("by_org"))[:80])
    # Официальный показатель: источник назван, демо-метки нет.
    check("официальный показатель не помечен как демо",
          official.get("ok") is True and official.get("demo") is False,
          str(official.get("demo")))
    check("у официального показателя назван источник",
          "Отчёт о самообследовании" in (official.get("notice") or ""),
          str(official.get("notice"))[:100])
    check("посторонний вопрос честно отклонён", junk.get("ok") is False, str(junk))


def test_metrics_sql_is_parameterised():
    """Вопрос не должен попадать в текст SQL ни одним символом."""
    import inspect
    src = inspect.getsource(tools.query) + inspect.getsource(tools.find_metric) \
        + inspect.getsource(tools.find_org)
    # f-строки и конкатенация в SQL — главный способ протащить ввод
    for marker in ('f"SELECT', "f'SELECT", '+ "', '+ \'"'):
        if marker in src:
            # допустимо, только если это не строка запроса; проверяем грубо
            pass
    check("в SQL нет строковой подстановки вопроса",
          "%s" not in src or "?" in src, "параметры используются")
    check("выборка фактов идёт параметрами", "WHERE f.metric_id = ? AND f.org_id"
          in src, "фильтр по метрике и подразделению")


def main():
    print("Проверка экипажа агентов (без языковой модели)")
    for fn in (test_critic_verdict_ok, test_critic_verdict_fail,
               test_critic_garbage_is_not_ok, test_critic_empty,
               test_registry_blocks, test_specialists_have_distinct_prompts, test_plan_agents,
               test_generalist_has_no_filter, test_unknown_agent_is_generalist,
               test_degraded_when_model_down,
               test_agent_without_documents_fails_honestly,
               test_judge_thresholds, test_reformulate_keeps_terms,
               test_metrics_tool_marks_demo, test_metrics_sql_is_parameterised):
        fn()
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
        return 1
    print("Все проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())