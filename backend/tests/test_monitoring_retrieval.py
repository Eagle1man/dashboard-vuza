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


if __name__ == "__main__":
    test_monitoring_documents_are_retrievable()
    print("поиск находит документы мониторинга")
