# -*- coding: utf-8 -*-
"""СПО: разбор карточки, места/медианы и текст для RAG — без сети."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import monitoring, spo  # noqa: E402


CARD_HTML = """
<table id="info">
  <tr><td>наименование образовательной организации</td><td>ЧОУ ВО "ЮЖНЫЙ УНИВЕРСИТЕТ (ИУБиП)"</td></tr>
  <tr><td>субъект РФ, адрес</td><td>Ростовская область</td></tr>
</table>
<table class="napde">
  <tr><td>Наименование показателя</td><td>Значение показателя по организации</td>
      <td>Значение по субъекту РФ</td><td>Значение по РФ</td></tr>
  <tr><td>1.1. Общая численность студентов</td><td>1 131</td><td>116 764</td><td>3 800 333</td></tr>
  <tr><td>1.2. Удельный вес очной формы</td><td>100,00</td><td>88,65</td><td>87,69</td></tr>
  <tr><td>4.1. Доля расходов на оборудование</td><td>0,00</td><td>0,12</td><td>2,90</td></tr>
</table>
"""

REGION_HTML = """
<a href="inst.php?id=1971">ЧОУ ВО "ЮЖНЫЙ УНИВЕРСИТЕТ (ИУБиП)"</a>
<a href="inst.php?id=111148">Донской банковский колледж</a>
<a href="inst.php?id=1971">дубль</a>
"""


def test_parse_card_reads_indicators():
    card = spo.parse_card(CARD_HTML)
    assert card["name"].startswith("ЧОУ ВО")
    assert set(card["indicators"]) == {"1.1", "1.2", "4.1"}
    assert card["indicators"]["1.1"]["org"] == 1131.0
    assert card["indicators"]["1.1"]["region"] == 116764.0
    assert card["indicators"]["1.1"]["rf"] == 3800333.0
    assert card["indicators"]["1.2"]["org"] == 100.0


def test_region_ids_deduplicates():
    ids = spo.region_ids(REGION_HTML)
    assert [item["id"] for item in ids] == ["1971", "111148"]
    assert "банковский" in ids[1]["name"]


def test_build_release_rank_median_delta():
    card = spo.parse_card(CARD_HTML)
    organizations = [
        {"id": "1971", "indicators": {"1.1": {"value": 1131.0}}},
        {"id": "2", "indicators": {"1.1": {"value": 2000.0}}},
        {"id": "3", "indicators": {"1.1": {"value": 500.0}}},
    ]
    previous = {"1.1": {"org": 1000.0}}
    release = spo.build_release(card, organizations, previous)
    row = release["1.1"]
    assert row["rank"] == 2  # одна организация строго выше
    assert row["total"] == 3
    assert row["percentile"] == 50
    assert row["median"] == 1131.0
    assert row["delta"] == 13.1


def test_build_release_ties_share_place():
    card = spo.parse_card(CARD_HTML)
    organizations = [
        {"id": "1971", "indicators": {"1.1": {"value": 1131.0}}},
        {"id": "2", "indicators": {"1.1": {"value": 1131.0}}},
        {"id": "3", "indicators": {"1.1": {"value": 500.0}}},
    ]
    row = spo.build_release(card, organizations)["1.1"]
    assert row["rank"] == 1
    assert row["percentile"] == 100


def test_spo_api_has_all_sections_and_units():
    """Подраздел СПО получает все 7 разделов и единицы измерения."""
    data = monitoring.spo()
    assert data["available"] is True
    assert [s["code"] for s in data["sections"]] == [str(i) for i in range(1, 8)]
    assert len(data["rows"]) == 20
    assert all(row["unit"] for row in data["rows"])


def test_spo_api_maps_changed_codes_for_2024():
    """Смена нумерации не должна оставлять 2024 год пустым."""
    data = monitoring.spo()
    by_code = {row["code"]: row for row in data["rows"]}
    assert by_code["5.2"]["years"]["2024"]["org"] == 41.46
    assert by_code["5.2"]["years"]["2024"].get("mapped") is True
    assert by_code["5.2"]["years"]["2025"]["delta"] == 24.3
    assert by_code["1.4"]["years"]["2024"]["org"] == 0.0
    # Для 1.3 сопоставимого значения в прежней методике нет.
    assert "2024" not in by_code["1.3"]["years"]


def test_write_spo_md(tmp_path):
    spo_data = {
        "years": {
            "2024": {"indicators": {"1.1": {"org": 1031.0}}},
            "2025": {"indicators": {
                "1.1": {"title": "Общая численность студентов", "org": 1131.0,
                        "region": 116764.0, "rf": 3800333.0,
                        "rank": 28, "total": 141},
            }},
        }
    }
    path = tmp_path / "2025-iubip.md"
    spo.write_spo_md(spo_data, "2025", path)
    text = path.read_text(encoding="utf-8")
    assert "ИУБиП: 1131.0" in text
    assert "место в области: 28 из 141" in text
    assert "в выпуске 2024: 1031.0" in text
