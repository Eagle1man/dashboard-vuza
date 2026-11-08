# -*- coding: utf-8 -*-
"""Публикационная активность: снимок платформы «Доступ».

Платформа живёт в вузовской сети (10.19.20.20:8080). Пароль в
репозитории не хранится: он читается из переменной SCIENCE_PASSWORD.
Если заданы SCIENCE_API_URL и пароль, модуль пытается обновить снимок
по API; иначе отдаёт последний сохранённый снимок.

РИНЦ и Scopus — разные учёты, поэтому они отдаются отдельными полями:
складывать их в одно число нельзя.
"""
import datetime
import json
import os
import time
import urllib.request

from . import config

SNAPSHOT = config.DATA_DIR / "science_publications.json"
CACHE = {"at": 0.0, "data": None}
TTL = 300


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _normalize(raw):
    """Приводит ответ платформы к схеме дашборда.

    Понимает наш снимок (publications/authors/...) и плоский ответ API
    (publications_total, authors_total, ...). Незнакомую схему не
    угадывает: возвращает None, чтобы не показать чужое число.
    """
    if not isinstance(raw, dict):
        return None
    pubs = raw.get("publications") if isinstance(raw.get("publications"), dict) else {}
    total = raw.get("publications_total", pubs.get("total"))
    if total is None:
        return None
    ids = raw.get("identifiers") if isinstance(raw.get("identifiers"), dict) else {}
    citations = raw.get("citations")
    if not isinstance(citations, list):
        citations = []
    try:
        return {
            "source": raw.get("source") or "Платформа «Доступ»",
            "snapshot_at": raw.get("snapshot_at") or _now(),
            "publications": {
                "total": int(total),
                "elibrary": int(raw.get("publications_elibrary", pubs.get("elibrary") or 0)),
                "scopus": int(raw.get("publications_scopus", pubs.get("scopus") or 0)),
                "demo": int(raw.get("publications_demo", pubs.get("demo") or 0)),
            },
            "authors": int(raw.get("authors", raw.get("authors_total") or 0)),
            "editions": int(raw.get("editions", raw.get("editions_total") or 0)),
            "duplicates_removed": int(raw.get("duplicates_removed", 0)),
            "annotations": int(raw.get("annotations", 0)),
            "identifiers": {
                "edn": int(raw.get("identifiers_edn", ids.get("edn") or 0)),
                "doi": int(raw.get("identifiers_doi", ids.get("doi") or 0)),
                "eid_scopus": int(raw.get("identifiers_eid_scopus", ids.get("eid_scopus") or 0)),
            },
            "citations": [int(x) for x in citations],
        }
    except (TypeError, ValueError):
        return None


def _fetch_live():
    """Пробует API платформы; без URL и пароля — None."""
    url = os.environ.get("SCIENCE_API_URL")
    password = os.environ.get("SCIENCE_PASSWORD")
    if not url or not password:
        return None
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "X-Password": password})
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return _normalize(raw)


def report():
    """Публикационная активность: live с платформы или последний снимок."""
    now = time.time()
    if CACHE["data"] is not None and now - CACHE["at"] < TTL:
        return CACHE["data"]

    live = None
    try:
        live = _fetch_live()
    except Exception:
        live = None

    if live:
        live["available"] = True
        live["mode"] = "live"
        live["updated_at"] = _now()
        CACHE.update({"at": now, "data": live})
        return live

    if not SNAPSHOT.exists():
        return {"available": False, "reason": "снимок публикационной активности не собран"}
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    data["available"] = True
    data["mode"] = "snapshot"
    data["updated_at"] = data.get("snapshot_at") or "—"
    CACHE.update({"at": now, "data": data})
    return data
