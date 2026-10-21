# -*- coding: utf-8 -*-
"""
Проверки выбора показателя по вопросу ректора.

Выбор показателя — не мелочь: не тот показатель в ответе хуже, чем
никакого. Раньше подсчёт брал по одному лучшему совпадению на метрику,
и вопросы вроде «какая успеваемость студентов» уходили в «Иностранных
студентов» — вес совпадения был одинаковый, и выигрывала та метрика,
что стояла в справочнике раньше.

Здесь зафиксированы настоящие вопросы ректора и ожидаемый показатель.
Список пополняется каждым новым неверным выбором: молчаливый возврат
ошибки в этой задаче стоит дороже всего остального.

Запуск: python -m backend.tests.test_metric_pick
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import db  # noqa: E402
from backend import tools  # noqa: E402

FAILURES = []

# (вопрос ректора, ожидаемый metric_id)
CASES = [
    ("какая успеваемость студентов", "uspevaemost"),
    ("сколько студентов в университете", "continuer-total"),
    ("какая численность обучающихся", "continuer-total"),
    ("сколько человек в магистратуре", "continuer-ma"),
    ("какой объём НИР", "nir"),
    ("доходы и расходы", "income"),
    ("доля аренды в расходах", "arenda-share"),
    ("трудоустройство выпускников", "trudoustroystvo"),
    ("библиотечный фонд", "biblioteka"),
    ("принято на первый курс в 2025", "applicant-2025"),
    ("какая доля задолженностей", "zadolzhennosti"),
    ("сколько отличников", "otlichniki"),
    ("какой средний балл ЕГЭ", "sredniy-ball"),
    ("доля субсидии в доходах", "subsidy-share"),
    ("фонд оплаты труда", "fot"),
    ("объём грантов", "granty"),
    ("патенты и РИД", "patenty"),
    ("наполняемость аудиторий", "napolnyaemost"),
    ("нагрузка ППС", "nagruzka-pps"),
    ("какая доля ФОТ", "fot-share"),
]


def check(name, condition, detail=""):
    if condition:
        print(f"  ОК     {name}")
    else:
        print(f"  ПРОВАЛ {name}  {detail}")
        FAILURES.append(name)


def test_pick():
    conn = db.connect()
    for question, expected in CASES:
        m = tools.find_metric(conn, question)
        got = m["metric_id"] if m else None
        check(f"вопрос «{question}»", got == expected,
              f"-> {got}, ожидался {expected}")
    conn.close()


def test_organizational_structure():
    """Структура подразделений: академия -> кафедра, без сирот."""
    conn = db.connect()
    acad = conn.execute(
        "SELECT COUNT(*) FROM dim_org WHERE kind='academy'").fetchone()[0]
    dept = conn.execute(
        "SELECT COUNT(*) FROM dim_org WHERE kind='dept'").fetchone()[0]
    orphan = conn.execute(
        "SELECT COUNT(*) FROM dim_org d WHERE d.kind='dept' AND NOT EXISTS"
        " (SELECT 1 FROM dim_org p WHERE p.org_id=d.parent_id"
        "  AND p.kind='academy')").fetchone()[0]
    check("5 академий и 9 кафедр", (acad, dept) == (5, 9),
          f"академий={acad} кафедр={dept}")
    check("у каждой кафедры есть академия", orphan == 0, f"сирот={orphan}")
    conn.close()


def test_official_has_no_history():
    """У официальных показателей одна точка и никакой разбивки.

    В документах университета разбивки по кафедрам нет, а исторического
    ряда нет тем более. Выдуманный ряд ректор прочтёт как историю.
    """
    conn = db.connect()
    rows = conn.execute(
        "SELECT metric_id, source, src_note FROM dim_metric"
        " WHERE source='official'").fetchall()
    check("официальные показатели есть", len(rows) > 0, f"найдено {len(rows)}")
    for r in rows:
        n = conn.execute(
            "SELECT COUNT(*) FROM fact_metric WHERE metric_id=? AND org_id='ALL'",
            (r["metric_id"],)).fetchone()[0]
        nd = conn.execute(
            "SELECT COUNT(*) FROM fact_metric WHERE metric_id=? AND org_id<>'ALL'",
            (r["metric_id"],)).fetchone()[0]
        check(f"{r['metric_id']}: одна точка, разбивки нет, источник указан",
              n == 1 and nd == 0 and bool(r["src_note"]),
              f"точек={n} разбивка={nd} источник={r['src_note']!r}")
    conn.close()


def main():
    print("Проверка выбора показателя")
    test_pick()
    test_organizational_structure()
    test_official_has_no_history()
    if FAILURES:
        print(f"\nПРОВАЛЕНО проверок: {len(FAILURES)}")
        return 1
    print("\nВсе проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
