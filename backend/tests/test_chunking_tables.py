# -*- coding: utf-8 -*-
"""Нарезка таблиц: строка «вуз — значение» не должна терять соседа.

Проверка появилась после замера: из 165 вопросов по региональной таблице
(выпуск 2025) нужное число доходило до модели только в половине случаев.
Причина — кусок таблицы на десять вузов и общий префикс официальных
названий: поиск находил «похожий» вуз. Здесь зафиксировано правило,
которое это лечит.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import chunking  # noqa: E402

TABLE = """# Вузы области, выпуск 2025

## E.1 Средний балл ЕГЭ

1. Федеральное государственное бюджетное образовательное учреждение «Ростовская консерватория» — 83.07
2. Ростовский институт (филиал) «Донской университет» — 76.09
3. Южный университет (ИУБиП) — 60.74 ← ИУБиП
"""


def test_each_table_row_is_its_own_chunk():
    chunks = chunking.chunk_text(TABLE, chunk_tokens=700, overlap=0)
    rows = [c for c in chunks if "—" in c["text"]]
    assert len(rows) == 3, f"строк {len(rows)}, ожидалось 3"
    assert rows[0]["heading"] == "E.1 Средний балл ЕГЭ"
    assert "83.07" in rows[0]["text"]
    assert "ИУБиП" in rows[2]["text"]
    # Название и его число обязаны быть в одном фрагменте.
    for c in rows:
        assert "—" in c["text"] and any(ch.isdigit() for ch in c["text"])


def test_plain_list_is_not_split_per_line():
    text = """## Требования

- документ об образовании
- паспорт
- заявление
"""
    chunks = chunking.chunk_text(text, chunk_tokens=700, overlap=0)
    assert len(chunks) == 1
    assert "паспорт" in chunks[0]["text"]


if __name__ == "__main__":
    test_each_table_row_is_its_own_chunk()
    test_plain_list_is_not_split_per_line()
    print("нарезка таблиц: строка не отрывается от значения")
