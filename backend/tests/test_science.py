# -*- coding: utf-8 -*-
"""Публикационная активность: снимок и нормализация ответа платформы."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import science  # noqa: E402


def test_snapshot_has_real_publication_data():
    data = science.report()
    assert data["available"] is True
    assert data["publications"]["total"] == 52
    assert data["publications"]["elibrary"] == 50
    assert data["publications"]["scopus"] == 2
    assert data["authors"] == 45
    assert data["editions"] == 29
    assert data["duplicates_removed"] == 3
    assert data["annotations"] == 0
    assert data["identifiers"] == {"edn": 3, "doi": 4, "eid_scopus": 2}
    assert data["citations"] == [48, 28, 22, 6, 4, 1]


def test_normalize_flat_api_response():
    raw = {
        "publications_total": 10,
        "publications_elibrary": 8,
        "publications_scopus": 2,
        "authors_total": 7,
        "editions_total": 4,
        "duplicates_removed": 1,
        "annotations": 0,
        "identifiers_edn": 1,
        "identifiers_doi": 2,
        "identifiers_eid_scopus": 3,
        "citations": [5, 3],
    }
    out = science._normalize(raw)
    assert out["publications"] == {"total": 10, "elibrary": 8, "scopus": 2, "demo": 0}
    assert out["authors"] == 7
    assert out["editions"] == 4
    assert out["citations"] == [5, 3]


def test_normalize_unknown_schema_is_not_guessed():
    assert science._normalize({"something": "else"}) is None
