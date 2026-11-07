# -*- coding: utf-8 -*-
"""Карточка мониторинга: полнота структуры и точность чисел.

Зачем отдельный тест. Раздел III собран с внешнего сайта, и ошибка
разбора (сдвиг колонок, потерянная строка) выглядит как правдоподобная
таблица. Здесь проверяются границы разделов, число показателей и
несколько чисел, которые легко сверить с первоисточником.

Запуск: python -m pytest backend/tests/test_monitoring_card.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import analytics, monitoring  # noqa: E402

SOURCE = ROOT / "knowledge" / "official" / "monitoring" / "source"


def _directions():
    return json.loads((SOURCE / "iubip-directions.json").read_text(encoding="utf-8"))


def _card():
    return json.loads((SOURCE / "iubip-card.json").read_text(encoding="utf-8"))


def _monitoring():
    return json.loads((ROOT / "data" / "monitoring.json").read_text(encoding="utf-8"))


def test_directions_structure():
    data = _directions()
    sections = data["sections"]
    assert [s["code"] for s in sections] == ["1", "2", "3", "4", "5", "6"]
    counts = {s["code"]: len(s["indicators"]) for s in sections}
    assert counts == {"1": 15, "2": 13, "3": 13, "4": 3, "5": 8, "6": 5}, counts
    assert sum(counts.values()) == 57
    codes = [i["code"] for s in sections for i in s["indicators"]]
    assert codes[0] == "1.1" and codes[-1] == "6.5"
    assert len(codes) == len(set(codes))


def test_directions_known_values():
    data = _directions()
    by_code = {i["code"]: i for s in data["sections"] for i in s["indicators"]}
    # Числа сверены с карточкой id=1971.
    assert by_code["1.3"]["value"] == 60.74
    assert by_code["2.3"]["value"] == 25.45
    assert by_code["2.4"]["value"] == 16500.0
    assert by_code["4.3"]["value"] == 313.14
    assert by_code["5.6"]["value"] == 0.32
    assert by_code["5.8"]["value"] == 134.73
    assert by_code["6.5"]["value"] == 51.67
    # Число строк не потерялось при разборе таблицы.
    assert by_code["1.15"]["value"] == 0.0
    assert by_code["3.13"]["value"] == 4282.0


def test_directions_medians_are_honest():
    data = _directions()
    by_code = {i["code"]: i for s in data["sections"] for i in s["indicators"]}
    # Отношение 1.0 — медиана восстановлена точно.
    assert by_code["2.6"]["median_private"] == 100.0
    assert by_code["2.6"]["median_private_capped"] is False
    # Обрезанная шкала: медиану не выдумываем.
    assert by_code["2.1"]["median_private"] is None
    assert by_code["2.1"]["median_private_capped"] is True
    # Нулевое значение: сравнения нет.
    assert by_code["1.1"]["median_private"] is None
    assert by_code["1.1"]["median_private_comparable"] is False


def test_card_sections():
    card = _card()
    assert card["contingent"]["total"] == 1350
    assert card["contingent"]["full_time"] == 427
    assert card["region"]["organizations"] == 38
    assert card["region"]["students"] == 139998
    assert len(card["ugns"]) == 5
    assert card["ugns"][0]["code"] == "09.00.00"
    assert len(card["branches"]) == 8
    numbered = [a for a in card["additional"] if not a["sub"]]
    assert len(numbered) == 56
    assert numbered[0]["value"] == 1350
    assert numbered[-1]["value"] == 100.0


def test_monitoring_has_five_year_series_and_medians():
    data = _monitoring()
    release = data["years"]["2025"]
    e1 = release["indicators"]["E1"]
    assert e1["values"] == {"2021": 55.12, "2022": 54.22, "2023": 55.51,
                            "2024": 57.42, "2025": 60.74}
    assert e1["delta_pct"] == 5.8
    assert e1["medians"] == {"rf": 62.8, "region": 59.0, "department": 53.19}
    assert e1["rank"] == 18 and e1["total"] == 38 and e1["percentile"] == 54
    # E.5 в выпуске 2025 не публикуется — строки нет, а не ноль.
    assert "E5" not in release["indicators"]
    assert "E.5" in data["notes"]["2025"]


def test_monitoring_api_exposes_new_fields():
    loaded = monitoring.load()
    assert loaded["available"] is True
    assert [a["code"] for a in loaded["e_axes"]] == ["E1", "E2", "E3", "E4", "E5", "E8"]
    release = loaded["years"]["2025"]
    assert release["values_years"][0] == "2021"
    assert release["indicators"]["E2"]["medians"]["rf"] == 132.1


def test_analytics_direction_lookup():
    stats = analytics.build(verbose=False)
    assert stats["directions"] == 57
    conn = analytics.connect()
    try:
        data = analytics.answer(conn, "Сколько публикаций в Белом списке у ИУБиП?")
        assert data is not None
        assert data["code"] == "2.3"
        assert data["numbers"][0] == 25.45
        assert "25.45" in data["text"] or "25,45" in data["text"]

        data = analytics.answer(conn, "Какая доля иностранных студентов из СНГ?")
        assert data is not None and data["code"] == "3.2"

        # Явный код E остаётся за E-показателем.
        data = analytics.answer(conn, "Какое значение E.1 у ИУБиП в 2024 году?")
        assert data is not None and data["code"] == "E1"

        # Карточка раздела III — только про ИУБиП: вопрос о другом вузе
        # не должен получать наше число.
        data = analytics.answer(conn, "Сколько публикаций в Белом списке у ЮФУ?")
        assert data is None or data.get("kind") != "direction", data
    finally:
        conn.close()
