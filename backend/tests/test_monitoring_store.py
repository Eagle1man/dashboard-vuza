# -*- coding: utf-8 -*-
"""Структурированная копия мониторинга: сборка, поля и контрольные числа."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import monitoring_store as store  # noqa: E402


def _rows(path):
    return [json.loads(line) for line in
            (path / "observations.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _find(rows, **kwargs):
    return [row for row in rows
            if all(row.get(key) == value for key, value in kwargs.items())]


def test_build_writes_complete_package(tmp_path):
    manifest = store.build(out_dir=tmp_path, verbose=False)
    for name in ("manifest.json", "schema.json", "README.md",
                 "organizations.json", "indicators.json", "observations.jsonl",
                 "observations.csv", "monitoring.sqlite"):
        assert (tmp_path / name).exists(), name
    for name in ("vo-2024", "vo-2025", "spo-2024", "spo-2025"):
        assert (tmp_path / "cards" / f"{name}.json").exists()

    rows = _rows(tmp_path)
    assert len(rows) == manifest["counts"]["observations"]
    assert len({row["obs_id"] for row in rows}) == len(rows)
    assert all(row["source_url"] for row in rows)
    assert all(row["obs_id"] for row in rows)
    assert manifest["counts"]["organizations"] == 180
    assert manifest["counts"]["indicators"] >= 180

    organizations = json.loads((tmp_path / "organizations.json")
                               .read_text(encoding="utf-8"))
    assert len(organizations) == 180
    iubip = [item for item in organizations if item["is_iubip"]]
    assert {item["level"] for item in iubip} == {"VO", "SPO"}
    assert all(item["profile"] for item in iubip)


def test_control_values_match_site(tmp_path):
    store.build(out_dir=tmp_path, verbose=False)
    rows = _rows(tmp_path)

    direction = _find(rows, level="VO", release=2025, code="1.3",
                      org_id="1971")[0]
    assert direction["value_num"] == 60.74
    assert direction["median_private"] == 53.71
    assert direction["median_region"] == 59.71
    assert direction["unit"] == "балл"

    e1 = _find(rows, level="VO", release=2025, code="E1",
               org_id="1971", year=2025)[0]
    assert e1["value_num"] == 60.74
    assert e1["place"] == 18
    assert e1["place_total"] == 38
    assert e1["median_region"] == 59.0

    ugn = _find(rows, level="VO", release=2025, code="38.00.00",
                org_id="1971", kind="count")
    assert any(row["value_num"] == 221.8 for row in ugn)

    spo = _find(rows, level="SPO", release=2025, code="1.1",
                org_id="1971")[0]
    assert spo["value_num"] == 1131.0
    assert spo["subject_value"] == 116764.0
    assert spo["rf_value"] == 3800333.0
    assert spo["place"] == 28
    assert spo["place_total"] == 141
    assert spo["median_org"] == 640.0

    mapped = _find(rows, level="SPO", release=2025, code="5.2",
                   org_id="1971")[0]
    assert mapped["prev_value"] == 41.46
    assert mapped["delta"] == 24.3
    assert mapped["extra"]["mapped_prev"] is True

    ugps = _find(rows, level="SPO", release=2025, code="09.00.00",
                 org_id="1971", kind="count")
    assert any(row["value_num"] == 6 for row in ugps)


def test_medians_only_for_latest_year(tmp_path):
    store.build(out_dir=tmp_path, verbose=False)
    rows = _rows(tmp_path)
    early = _find(rows, level="VO", release=2024, code="E1",
                  org_id="337", year=2021)[0]
    latest = _find(rows, level="VO", release=2024, code="E1",
                   org_id="337", year=2024)[0]
    assert early["median_region"] is None
    assert early["median_rf"] is None
    assert latest["median_region"] is not None
    assert latest["median_rf"] is not None


def test_answer_question_uses_store(tmp_path):
    store.build(out_dir=tmp_path, verbose=False)
    value = store.answer_question(
        "Какое значение показателя 1.3 у ИУБиП?", path=tmp_path)
    assert value and "60,74" in value["text"]
    ugn = store.answer_question(
        "Каков контингент по УГН 38.00.00 у ИУБиП?", path=tmp_path)
    assert ugn and "221,8" in ugn["text"]
    spo = store.answer_question(
        "Какое значение показателя 1.1 у ИУБиП в СПО?", path=tmp_path)
    assert spo and "1131" in spo["text"]
    web = store.answer_question(
        "Что известно о веб-сайте ИУБиП?", path=tmp_path)
    assert web and "iubip.ru" in web["text"]
    assert store.answer_question("Сколько студентов в ЮФУ?",
                                 path=tmp_path) is None


def test_sqlite_lookup_and_counts(tmp_path):
    store.build(out_dir=tmp_path, verbose=False)
    conn = sqlite3.connect(tmp_path / "monitoring.sqlite")
    total = conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    assert total == len(_rows(tmp_path))
    value = conn.execute(
        "SELECT value_num, median_private, median_region FROM observation "
        "WHERE level='VO' AND release=2025 AND code='1.3' AND org_id='1971'"
    ).fetchone()
    assert value == (60.74, 53.71, 59.71)
    conn.close()

    found = store.lookup("SPO", 2025, "1.1", "1971", path=tmp_path)
    assert found[0]["value_num"] == 1131.0
    assert found[0]["place"] == 28
