# -*- coding: utf-8 -*-
"""Мониторинг эффективности: место вуза среди вузов Ростовской области.

Данные — из открытых материалов monitoring.miccedu.ru за 2024 и 2025
годы. Файл `data/monitoring.json` собирается отдельным скриптом: по
региональной сводке обходятся карточки всех вузов области, иначе место
не посчитать — на сводной странице только агрегаты.

Процентиль здесь — доля вузов области, которые вуз обгоняет по этому
показателю: 100 значит «лучший в области», 50 — «ровно середина».
Так разные по единицам показатели (баллы, тысячи рублей, проценты)
можно положить на один радар.
"""
import json

from . import config

CACHE = {"mtime": None, "data": None}


def _empty():
    return {"available": False, "axes": [], "years": {}, "table": []}


def load():
    """Читает data/monitoring.json; при отсутствии — честный «нет данных»."""
    path = config.DATA_DIR / "monitoring.json"
    if not path.exists():
        return _empty()

    mtime = path.stat().st_mtime
    if CACHE["data"] is not None and CACHE["mtime"] == mtime:
        return CACHE["data"]

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        data = _empty()
        data["error"] = f"{type(exc).__name__}: {exc}"
        return data

    years = sorted(raw.get("years", {}).keys())
    axes = raw.get("axes", [])

    table = []
    for axis in axes:
        code = axis["code"]
        row = {"code": code, "label": axis["label"], "unit": axis["unit"],
               "years": {}}
        for year in years:
            ind = (raw["years"].get(year, {}).get("indicators") or {}).get(code)
            if ind:
                row["years"][year] = {
                    "value": ind["value"],
                    "values": ind.get("values") or {},
                    "delta_pct": ind.get("delta_pct"),
                    "medians": ind.get("medians") or {},
                    "rank": ind["rank"],
                    "total": ind["total"],
                    "percentile": ind["percentile"],
                    "median": ind["median"],
                    "best": ind.get("best"),
                }
        table.append(row)

    data = {
        "available": True,
        "source": raw.get("source"),
        "vuz": raw.get("vuz", {}),
        "axes": axes,
        "e_axes": raw.get("e_axes") or axes,
        "notes": raw.get("notes") or {},
        "years": {
            year: {
                "total": raw["years"][year].get("total"),
                "average_percentile": raw["years"][year].get("average_percentile"),
                "values_years": raw["years"][year].get("years") or [],
                "indicators": raw["years"][year].get("indicators") or {},
            }
            for year in years
        },
        "series": {
            year: [
                (raw["years"][year]["indicators"].get(a["code"]) or {}).get("percentile")
                for a in axes
            ]
            for year in years
        },
        "table": table,
    }
    CACHE.update({"mtime": mtime, "data": data})
    return data


def _board_path(name: str):
    return config.ROOT / "knowledge" / "official" / "monitoring" / "source" / name


def directions():
    """Раздел III: результаты по направлениям деятельности (57 показателей).

    Источник — iubip-directions.json, собранный backend.collect_monitoring
    из карточки monitoring.miccedu.ru. Если файла нет, отдаём
    available=false, а не пустые таблицы.
    """
    path = _board_path("iubip-directions.json")
    if not path.exists():
        return {"available": False, "sections": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "available": True,
        "source": raw.get("source"),
        "release": raw.get("release"),
        "vuz": raw.get("vuz", {}),
        "sections": raw.get("sections") or [],
    }


def card():
    """Разделы I и IV: общие сведения, контингент и УГН(С)."""
    path = _board_path("iubip-card.json")
    if not path.exists():
        return {"available": False}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "available": True,
        "source": raw.get("source"),
        "release": raw.get("release"),
        "vuz": raw.get("vuz", {}),
        "info": raw.get("info") or {},
        "contingent": raw.get("contingent") or {},
        "region": raw.get("region") or {},
        "branches": raw.get("branches") or [],
        "ugns": raw.get("ugns") or [],
        "additional": raw.get("additional") or [],
        "e_table": raw.get("e_table") or {},
    }


def spo():
    """СПО: значения вуза, области и РФ плюс место в области.

    Отличие от ВО: в карточке СПО сразу три колонки — организация,
    субъект и РФ, поэтому сравнение с областью есть в первоисточнике.
    Обход всех карточек нужен только для места.
    """
    path = config.DATA_DIR / "spo.json"
    if not path.exists():
        return {"available": False, "years": [], "rows": []}

    raw = json.loads(path.read_text(encoding="utf-8"))
    years = sorted(raw.get("years", {}).keys())
    if not years:
        return {"available": False, "years": [], "rows": []}

    units = {"1.1": "чел.", "1.2": "%", "1.3": "балл", "1.4": "%", "2.1": "%", "2.2": "%"}
    last = raw["years"][years[-1]].get("indicators") or {}

    rows = []
    for code in last.keys():
        row = {"code": code, "title": last[code]["title"], "unit": units.get(code, ""), "years": {}}
        for year in years:
            ind = (raw["years"][year].get("indicators") or {}).get(code)
            if not ind:
                continue
            row["years"][year] = {
                "org": ind["org"], "region": ind["region"], "rf": ind["rf"],
                "rank": ind.get("rank"), "total": ind.get("total"),
                "percentile": ind.get("percentile"), "median": ind.get("median"),
                "delta": ind.get("delta"),
            }
        rows.append(row)

    return {
        "available": True,
        "source": raw.get("source"),
        "years": years,
        "total": raw["years"][years[-1]].get("total"),
        "rows": rows,
    }


def short_label(label: str) -> str:
    """«E.1. Образовательная деятельность» -> «Образовательная деятельность»."""
    text = label.split(".", 2)[-1].strip() if label.startswith("E.") else label
    return text


def _number(text: str):
    """«1 350», «60,36», «16 500,0», «–» -> float или None."""
    if text is None:
        return None
    clean = str(text).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def _norm_title(title: str) -> str:
    """Названия показателей между выпусками слегка меняются («интернов»
    убрали из перечисления). Сравниваем по словам без скобок и знаков."""
    import re as _re
    text = (title or "").lower()
    text = _re.sub(r"\(.*?\)", "", text)
    text = _re.sub(r"[^а-яёa-z0-9]+", " ", text)
    return _re.sub(r"\s+", " ", text).strip()


def detail():
    """Все показатели карточки вуза за 2024 и 2025 с изменением.

    Источник — knowledge/official/monitoring/source/iubip-detail.json,
    собранный из карточек monitoring.miccedu.ru. Пары ищем по названию:
    часть формулировок между выпусками меняется, поэтому совпадает 52 из
    57, а остальные показываем с прочерком в одном из годов.
    """
    path = config.DATA_DIR.parent / "knowledge" / "official" / "monitoring" / "source" / "iubip-detail.json"
    if not path.exists():
        return {"available": False, "rows": [], "years": []}

    raw = json.loads(path.read_text(encoding="utf-8"))
    years = sorted(raw.keys())
    if len(years) < 2:
        return {"available": False, "rows": [], "years": years}

    first, last = years[0], years[-1]
    by_first = {_norm_title(r["title"]): r for r in raw[first]}

    rows, used = [], set()
    for row in raw[last]:
        key = _norm_title(row["title"])
        pair = by_first.get(key)
        if pair:
            used.add(key)
        old = _number(pair["value"]) if pair else None
        new = _number(row["value"])
        delta = None
        if old is not None and new is not None and old != 0:
            delta = round((new - old) / abs(old) * 100, 1)
        rows.append({
            "title": row["title"],
            "unit": row["unit"],
            first: old,
            last: new,
            "delta": delta,
            "raw_first": pair["value"] if pair else None,
            "raw_last": row["value"],
        })

    for row in raw[first]:
        key = _norm_title(row["title"])
        if key in used:
            continue
        rows.append({
            "title": row["title"],
            "unit": row["unit"],
            first: _number(row["value"]),
            last: None,
            "delta": None,
            "raw_first": row["value"],
            "raw_last": None,
        })

    grew = sum(1 for r in rows if r["delta"] is not None and r["delta"] > 0)
    fell = sum(1 for r in rows if r["delta"] is not None and r["delta"] < 0)
    flat = sum(1 for r in rows if r["delta"] == 0)
    return {
        "available": True,
        "years": years,
        "rows": rows,
        "totals": {"all": len(rows), "grew": grew, "fell": fell, "flat": flat,
                   "comparable": grew + fell + flat},
    }
