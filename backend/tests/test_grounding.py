# -*- coding: utf-8 -*-
"""Проверка ответа на выдуманные числа и ссылки.

Каждый случай — реальный способ ошибиться, а не выдуманный для теста:
1.5B уже отвечала «У — от 35% до 45%, СБ — 85» по документу, где таких
чисел нет. Если эта проверка сломается, ошибка вернётся к человеку.

Запуск: python -m backend.tests.test_grounding
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import grounding  # noqa: E402

CONTEXT = (
    "1 курс П У СБ отч 09.03.03 Прикладная информатика 15 30% 50% 4,2 2 "
    "38.03.01 Экономика 14 60% 75% 4,5 1 Всего 2946 обучающихся. "
    "Успеваемость 87,4 % за 2025-2026 учебный год."
)


def _verify(answer, context=CONTEXT, n=6):
    return grounding.verify(answer, context, n)


def test_number_from_context_survives():
    text, rep = _verify("Посещаемость 60 %, успеваемость 75 % [1].")
    assert "60" in text and "75" in text, text
    assert rep["removed"] == [], rep


def test_thousands_and_commas_are_normalised():
    text, rep = _verify("Всего 2 946 обучающихся, успеваемость 87,4 % [2].")
    assert text.strip(), rep
    assert rep["removed"] == [], rep


def test_invented_number_removes_sentence():
    answer = ("Успеваемость высокая. У — от 35% до 45%, СБ — 85. "
              "Посещаемость 60 % [1].")
    text, rep = _verify(answer)
    assert "35" not in text and "45" not in text and "85" not in text, text
    assert "60" in text, text
    assert rep["removed"], rep
    assert "35" in rep["unsupported_numbers"] or "45" in rep["unsupported_numbers"], rep


def test_all_invented_gives_empty_text():
    text, rep = _verify("Средний балл 4,9, качество знаний 99 %.")
    assert text.strip() == "", repr(text)
    assert rep["ok"] is False, rep


def test_list_numbering_is_not_a_fact():
    answer = ("1. Всего 2946 обучающихся [1].\n"
              "2. Успеваемость 87,4 % [2].")
    text, rep = _verify(answer)
    assert text.startswith("1. "), text
    assert "2946" in text and "87,4" in text, text
    assert rep["removed"] == [], rep


def test_citation_out_of_range_is_removed():
    text, rep = _verify("Всего 2946 обучающихся [1,7].", n=6)
    assert "[7]" not in text, text
    assert "[1]" in text, text
    assert rep["bad_citations"] == [7], rep


def test_citation_on_missing_chunk_only():
    text, rep = _verify("Всего 2946 обучающихся [9].", n=6)
    assert "[9]" not in text, text
    assert rep["cited"] == [], rep
    assert "не поставила ссылки" in grounding.note(rep), grounding.note(rep)


def test_no_citations_is_reported():
    text, rep = _verify("Всего 2946 обучающихся.")
    assert rep["cited"] == [], rep
    assert "не поставила ссылки" in grounding.note(rep)


def test_removed_numbers_are_listed_in_note():
    text, rep = _verify("Средний балл 4,9. Всего 2946 обучающихся [1].")
    note = grounding.note(rep)
    assert "4,9" in note, note
    assert "убрала" in note, note


def test_repeats_are_collapsed():
    """1.5B уходит в цикл: один и тот же пункт идёт десяток раз."""
    answer = ("Всего 2946 обучающихся [1].\n"
              "1. Одобрить комплекс мер [2].\n"
              "2. Одобрить комплекс мер [2].\n"
              "3. Одобрить комплекс мер [2].\n"
              "4. Одобрить комплекс мер [2].")
    text, rep = _verify(answer)
    assert text.count("Одобрить комплекс мер") == 1, text
    assert len(rep["repeated"]) == 3, rep
    assert "повторы" in grounding.note(rep), grounding.note(rep)


def main():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ОК     {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  ПРОВАЛ {name}: {exc}")
    print()
    if failures:
        print(f"Провалов: {failures}")
        return 1
    print("Все проверки прошли")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
