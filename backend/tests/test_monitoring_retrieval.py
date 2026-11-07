# -*- coding: utf-8 -*-
"""Проверка, что новые документы мониторинга действительно находятся.

Это тест поиска, а не модели: он не зависит от качества LLM и отвечает на
вопрос «попадает ли нужный документ в контекст». Если поиск не находит
источник, никакая дообученная модель не спасёт — она не увидит факт.

Запуск: python -m pytest backend/tests/test_monitoring_retrieval.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import db, retrieve  # noqa: E402

# Вопрос -> документы, любой из которых обязан попасть в выдачу.
CASES = [
    ("Какое место ИУБиП по объёму НИОКР среди вузов Ростовской области?",
     ["official/monitoring/summary.md", "official/monitoring/region-2025.md"]),
    ("Средний балл ЕГЭ у ИУБиП в 2024 году",
     ["official/monitoring/2024-iubip.md", "official/monitoring/region-2024.md"]),
    ("Сколько студентов в ИУБиП по данным мониторинга 2025 года",
     ["official/monitoring/2025-iubip.md", "official/monitoring/summary.md"]),
    ("Средний балл аттестата в СПО",
     ["official/monitoring/spo/2025-iubip.md"]),
    ("Доходы вуза из всех источников по мониторингу",
     ["official/monitoring/2025-iubip.md", "official/monitoring/2024-iubip.md"]),
]


def test_monitoring_documents_are_retrievable():
    conn = db.connect()
    db.init_db(conn)
    failures = []
    for question, expected in CASES:
        chunks, _stats = retrieve.search(conn, question, top_k=6)
        found = [c["rel_path"] for c in chunks]
        if not any(path in found for path in expected):
            failures.append({"question": question, "expected": expected, "found": found[:6]})
    conn.close()
    assert not failures, f"поиск не нашёл документы мониторинга: {failures}"


def _values_in_context(question, values, top_k=6):
    conn = db.connect()
    db.init_db(conn)
    chunks, _ = retrieve.search(conn, question, top_k=top_k)
    text = " ".join(c["text"] for c in chunks).replace(",", ".")
    conn.close()
    return {v: (v in text) for v in values}


def test_comparison_retrieves_both_universities():
    """Сравнение двух вузов обязано принести обе строки, а не одну.

    Замер до исправлений: 0 из 15 сравнений находили оба числа. Причина —
    в разделе 38 строк, а длинное официальное название конкурента даёт
    десяток совпавших слов; строка ИУБиП уходила за 120-е место.
    """
    question = ("На сколько значение показателя «E.1 Средний балл ЕГЭ» "
                "у ИУБиП отличается от значения у вуза «Ростовский-на-Дону "
                "филиал федерального государственного бюджетного "
                "образовательного учреждения высшего образования "
                "«Всероссийский государственный университет кинематографии "
                "имени С.А. Герасимова»» в выпуске 2025?")
    found = _values_in_context(question, ["60.74", "76.09"])
    assert found == {"60.74": True, "76.09": True}, found


def test_region_row_value_is_in_context():
    """Строка «вуз — значение» не должна терять своё число."""
    question = ("Какое значение показателя «E.2 Объём НИОКР на одного НПР, "
                "тыс. руб.» у ИУБиП в выпуске 2025?")
    found = _values_in_context(question, ["419.85"])
    assert found == {"419.85": True}, found


if __name__ == "__main__":
    test_monitoring_documents_are_retrievable()
    print("поиск находит документы мониторинга")
