# -*- coding: utf-8 -*-
"""Сторож мониторинга: отпечатки, разбор ссылок и запись без сети.

Сетевые запросы здесь не проверяются: тест не должен зависеть от
доступности monitoring.miccedu.ru. Проверяется то, что решает,
перезаписывать ли файлы, и то, что запись не теряет данные.

Запуск: python -m pytest backend/tests/test_sync_monitoring.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import collect_monitoring, sync_monitoring  # noqa: E402


def test_fingerprint_stable_and_sensitive():
    a = {"value": 60.74, "years": ["2021", "2025"]}
    b = {"years": ["2021", "2025"], "value": 60.74}  # другой порядок ключей
    assert sync_monitoring.fingerprint(a) == sync_monitoring.fingerprint(b)
    c = {"value": 60.75, "years": ["2021", "2025"]}
    assert sync_monitoring.fingerprint(a) != sync_monitoring.fingerprint(c)


def test_site_updated_parses_date():
    text = "Результаты ... 2025 года обновлены 03.02.2026 г. в соответствии с письмом"
    assert sync_monitoring.site_updated(text) == "03.02.2026"
    assert sync_monitoring.site_updated("без даты") is None


def test_region_ids_deduplicates_and_cleans():
    html = """
    <a href="inst.php?id=1971">Частное образовательное учреждение "ЮЖНЫЙ УНИВЕРСИТЕТ (ИУБиП)"</a>
    <a href="/iam/2025/_vpo/inst.php?id=337"><b>Южный</b> федеральный университет</a>
    <a href="inst.php?id=1971">дубль</a>
    <a href="material.php?type=2&id=10703">область</a>
    """
    ids = sync_monitoring.region_ids(html)
    assert [item["id"] for item in ids] == ["1971", "337"]
    assert "ИУБиП" in ids[0]["name"]
    assert ids[1]["name"] == "Южный федеральный университет"


def test_region_entry_builds_indicators():
    directions = {"release": "2025", "sections": []}
    card = {
        "info": {"Наименование образовательной организации": "ИУБиП"},
        "e_table": {"indicators": [
            {"code": "E1", "title": "Средний балл ЕГЭ",
             "values": {"2021": 55.12, "2025": 60.74},
             "medians": {"rf": 62.8, "region": 59.0, "department": 53.19}},
        ]},
    }
    entry = sync_monitoring.region_entry(directions, card)
    assert entry["id"] == "1971"
    assert entry["name"] == "ИУБиП"
    assert entry["indicators"]["E1"]["values"]["2025"] == 60.74
    assert entry["indicators"]["E1"]["medians"]["region"] == 59.0


def test_quick_update_keeps_other_universities():
    old = [{"id": "1971", "name": "old"}, {"id": "337", "name": "ЮФУ"}]
    entry = {"id": "1971", "name": "new"}
    out = sync_monitoring.quick_update(old, entry)
    assert len(out) == 2
    assert next(e for e in out if e["id"] == "337")["name"] == "ЮФУ"
    assert next(e for e in out if e["id"] == "1971")["name"] == "new"


def test_save_roundtrip(tmp_path):
    source = ROOT / "knowledge" / "official" / "monitoring" / "source"
    directions = json.loads((source / "iubip-directions.json").read_text(encoding="utf-8"))
    card = json.loads((source / "iubip-card.json").read_text(encoding="utf-8"))
    info = collect_monitoring.save(directions, card,
                                   source_dir=tmp_path / "source",
                                   kb=tmp_path / "kb")
    saved = json.loads((tmp_path / "source" / "iubip-directions.json")
                       .read_text(encoding="utf-8"))
    assert saved["sections"] == directions["sections"]
    assert info["directions"] == 57
    assert (tmp_path / "kb" / "2025-directions.md").exists()
    assert (tmp_path / "kb" / "2025-card.md").exists()
    detail = json.loads((tmp_path / "source" / "iubip-detail.json")
                        .read_text(encoding="utf-8"))
    assert len(detail["2025"]) == 56
