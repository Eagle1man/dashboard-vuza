# -*- coding: utf-8 -*-
"""
Проверка качества поиска по корпусу.

Зачем нужен именно такой тест. Проверка «поиск вернул хоть что-то»
проходит и тогда, когда он возвращает не то: на вопрос про аттестацию
можно выдать регламент об отчислениях, и всё формально будет «зелёным».
Поэтому у каждого вопроса указан документ, в котором ответ находится,
и проверяется попадание именно его.

Запуск: python -m backend.tests.test_retrieval
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import config, db, retrieve  # noqa: E402

# (вопрос, ожидаемый файл документа, документов в выдаче достаточно)
#
# Проверки идут по НАСТОЯЩИМ документам университета. Раньше вопросы
# были про демонстрационные файлы, и это было правильно, пока корпус
# состоял только из них. Теперь, когда в корпусе лежат отчёт о
# самообследовании, план ФХД и материалы ВНОКО, проверять надо по ним:
# демо-документы в выдачу не попадают (см. retrieve._demo_clause), и
# вопрос про трудоустройство обязан находиться в отчёте о
# самообследовании, а не в выдуманном «аналитическом отчёте».
CASES = [
    ("численность студентов по уровням образования", "Отчёт о самообследовании", 3),
    ("доходы и расходы университета в плане на 2026 год", "План ФХД", 3),
    ("результаты опросов обучающихся и работодателей", "Инструментарий ВНОКО", 3),
    ("организационная структура университета на 2026 2027 учебный год",
     "Организационная структура", 3),
    ("успеваемость обучающихся в первом семестре 2025 2026", "Итоги успеваемости", 3),
    ("контроль уровня подготовленности обучающихся", "Контроль уровня подготовленности", 3),
    ("объём научных исследований за 2025 год", "Отчёт о самообследовании", 3),
]


def main():
    conn = db.connect()
    db.init_db(conn)
    n_docs = conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
    if n_docs == 0:
        print("КОРПУС ПУСТ — сначала python -m backend.ingest")
        return 2
    n_official = conn.execute(
        "SELECT COUNT(*) FROM doc WHERE is_demo = 0").fetchone()[0]
    n_demo = conn.execute(
        "SELECT COUNT(*) FROM doc WHERE is_demo = 1").fetchone()[0]
    print(f"корпус: {n_official} настоящих, {n_demo} демонстрационных")

    top_k = config.RETRIEVE_TOP_K
    passed, failed = 0, []

    for question, expected, allowed_rank in CASES:
        results, stats = retrieve.search(conn, question, top_k=top_k)
        titles = [r["rel_path"] for r in results]
        hit_rank = next(
            (i + 1 for i, p in enumerate(titles) if expected in p), None)
        mode = stats.get("fusion")
        if hit_rank and hit_rank <= allowed_rank:
            passed += 1
            print(f"  ОК   ({hit_rank}-е место, {mode:9}) {question}")
        else:
            failed.append((question, expected, hit_rank, titles, mode))
            print(f"  ПРОВАЛ ({mode:9}) {question}")
            print(f"         ожидали {expected}, получили {titles[:4]}")

    # Демо-документы не должны попадать в выдачу, пока в корпусе есть
    # настоящие: в них вымышленные числа, и ректор прочтёт «20 800
    # студентов» как число университета.
    if n_official:
        results, _ = retrieve.search(conn, "сколько студентов в университете",
                                     top_k=top_k)
        demo_hits = [r["rel_path"] for r in results
                     if r["rel_path"].startswith("demo/")]
        if demo_hits:
            failed.append(("демо-документы", "не должны попадать в выдачу",
                           None, demo_hits, "—"))
            print("  ПРОВАЛ демо-документы попали в выдачу: %s" % demo_hits[:3])
        else:
            passed += 1
            print("  ОК   демо-документы не попадают в выдачу при наличии настоящих")

    print(f"\nРезультат: {passed} из {len(CASES) + (1 if n_official else 0)} "
          f"проверок пройдено")
    _ = top_k
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())