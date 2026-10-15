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
CASES = [
    ("почему выросло отчисление студентов", "05_reglament", 3),
    ("как проходит аттестация преподавателей", "03_polozhenie_o_attestacii", 3),
    ("рентабельность университета и причины её снижения", "06_finansovyy", 3),
    ("трудоустройство выпускников по факультетам", "04_otchet", 3),
    ("кворты приёма на бюджетные места", "01_polozhenie_o_prieme", 3),
    ("какие цифровые сервисы внедрены", "07_strategiya", 4),
    ("языковая аттестация иностранных студентов", "08_polozhenie", 3),
]


def main():
    conn = db.connect()
    db.init_db(conn)
    n_docs = conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
    if n_docs == 0:
        print("КОРПУС ПУСТ — сначала python -m backend.ingest")
        return 2

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

    print(f"\nРезультат: {passed} из {len(CASES)} вопросов "
          f"нашли нужный документ в топ-{allowed_rank}")
    _ = top_k
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())