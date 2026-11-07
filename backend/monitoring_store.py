# -*- coding: utf-8 -*-
"""Структурированная копия карточек monitoring.miccedu.ru.

Зачем отдельный слой. В проекте уже есть разобранные JSON по разделам
(карточка ВО, 57 направлений, доп. характеристики, СПО, регион), но они
лежат в разных форматах и читаются разным кодом. Модели и датасету нужен
один плоский набор фактов с одинаковыми полями, единицами и источником.

Что собирается:

    data/monitoring/structured/
        manifest.json       источники, выпуски, счётчики, sha256
        schema.json         описание полей
        README.md           как читать: выпуск ≠ год данных, медиана ≠ значение
        organizations.json  180 организаций (38 ВО + 142 СПО)
        indicators.json     справочник показателей
        observations.jsonl  одна строка = один факт
        observations.csv    то же для Excel
        monitoring.sqlite   таблицы org / indicator / observation
        cards/*.json        вложенные полные карточки по выпускам

Запуск:
    python -m backend.monitoring_store build
    python -m backend.monitoring_store build --out D:\\tmp\\store
    python -m backend.monitoring_store lookup --level VO --release 2025 --code 1.3
    python -m backend.monitoring_store stats
"""
import argparse
import csv
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
KB = ROOT / "knowledge" / "official" / "monitoring"
SRC = KB / "source"
DATA = ROOT / "data"
OUT = DATA / "monitoring" / "structured"

VO_CARD = SRC / "iubip-card.json"
VO_DIRECTIONS = SRC / "iubip-directions.json"
VO_DETAIL = SRC / "iubip-detail.json"
VO_REGION = SRC / "region_indicators.json"
SPO_JSON = DATA / "spo.json"
SPO_REGION = SRC / "spo_region.json"
MONITORING_JSON = DATA / "monitoring.json"

VO_URL = "https://monitoring.miccedu.ru/iam/2025/_vpo/inst.php?id=1971"
SPO_URL = "https://monitoring.miccedu.ru/iam/2025/_spo/inst.php?id=1971"
VO_URL_2024 = "https://monitoring.miccedu.ru/iam/2024/_vpo/inst.php?id=1971"
SPO_URL_2024 = "https://monitoring.miccedu.ru/iam/2024/_spo/inst.php?id=1971"
VO_SOURCE_FILE = "knowledge/official/monitoring/source/iubip-card.json"
IUBIP_ID = "1971"
IUBIP_NAME = ("Частное образовательное учреждение высшего образования "
              "«ЮЖНЫЙ УНИВЕРСИТЕТ (ИУБиП)»")

FIELDS = [
    "obs_id", "level", "release", "data_year", "scheme", "org_id", "org_name",
    "is_iubip", "section", "block", "code", "title", "unit", "kind", "year",
    "value_num", "value_text", "place", "place_total", "percentile",
    "median_private", "median_region", "median_rf", "median_department",
    "median_org", "subject_value", "rf_value", "prev_value", "delta",
    "source_url", "source_file", "extra",
]

SCHEMA = {
    "obs_id": "стабильный идентификатор факта",
    "level": "VO — высшее образование, SPO — среднее профессиональное",
    "release": "год выпуска мониторинга",
    "data_year": "год, который описывает выпуск; release и data_year не совпадают",
    "scheme": "схема нумерации показателей (для СПО 2024 года — spo-2024)",
    "org_id": "идентификатор организации на сайте",
    "org_name": "название организации",
    "is_iubip": "признак ИУБиП",
    "section": "раздел карточки: I–V для ВО, 1–7 для показателей СПО",
    "block": "подраздел: направление деятельности или отрасль",
    "code": "код показателя (1.3, E1, V.07, 09.00.00 и т. п.)",
    "title": "название показателя",
    "unit": "единица измерения",
    "kind": "value — значение, median — медиана, profile — сведения, "
            "count/share — структурный факт",
    "year": "колонка года на сайте; для выпуска 2025 это 2021–2025",
    "value_num": "числовое значение, если его можно разобрать",
    "value_text": "значение как в первоисточнике",
    "place": "место среди организаций региона",
    "place_total": "сколько организаций участвовало в сравнении",
    "percentile": "процентиль: 100 — лучший в регионе, 50 — медиана",
    "median_private": "медиана частных вузов (ВО, раздел III)",
    "median_region": "медиана по региону",
    "median_rf": "медиана по РФ",
    "median_department": "медиана по ведомственной принадлежности",
    "median_org": "медиана по организациям региона (СПО)",
    "subject_value": "значение/итог по субъекту РФ (не медиана!)",
    "rf_value": "значение/итог по РФ (не медиана!)",
    "prev_value": "значение предыдущего выпуска, если сопоставимо",
    "delta": "изменение к предыдущему выпуску, %",
    "source_url": "адрес страницы-источника",
    "source_file": "локальный файл, из которого взят факт",
    "extra": "дополнительные поля источника (ratio, capped, best, counts)",
}

README = """# Структурированная копия monitoring.miccedu.ru

Единый набор фактов по карточкам ВО и СПО ИУБиП (id=1971) и региональным
таблицам. Собран из уже разобранных JSON проекта, без обращения к сети.

## Главное, что нужно помнить

- **release и data_year — разные вещи.** Выпуск 2025 года описывает данные
  2024 года. В файле есть оба поля, путать их нельзя.
- **median_region и subject_value — разные вещи.** Медиана — середина
  списка организаций. Значение по субъекту РФ — итог по региону (например,
  всего студентов). Для СПО на сайте рядом стоят оба, и они не взаимозаменяемы.
- **Место считается среди организаций региона**, а не среди всех вузов РФ.
  `place_total` показывает, сколько организаций участвовало.
- **Процентиль 100 — лучший результат в регионе**, 50 — медиана.
- **Пустое поле — не ноль.** Если значения нет на сайте или показатель не
  публикуется, `value_num` = null, а причина — в `extra`.
- **Схемы СПО 2024 и 2025 не совпадают.** В 2024 году была другая
  нумерация; поле `scheme` это фиксирует, а `prev_value` заполняется только
  для смысловых эквивалентов.

## Файлы

| Файл | Что внутри |
|---|---|
| `manifest.json` | источники, даты, счётчики, контрольные суммы |
| `schema.json` | описание каждого поля |
| `organizations.json` | 38 организаций ВО и 142 СПО |
| `indicators.json` | справочник показателей: код, название, единица, раздел |
| `observations.jsonl` | один факт на строку — основной файл |
| `observations.csv` | то же для Excel |
| `monitoring.sqlite` | те же данные с индексами для запросов |
| `cards/*.json` | вложенные карточки по выпускам |

## Примеры запросов

```sql
-- Средний балл ЕГЭ платников ИУБиП в выпуске 2025
SELECT value_num, unit, median_private, median_region
FROM observation
WHERE level='VO' AND release=2025 AND code='1.3' AND org_id='1971';

-- Все 20 показателей СПО с местом в области
SELECT code, title, value_num, place, place_total, median_org
FROM observation
WHERE level='SPO' AND release=2025 AND org_id='1971' AND kind='value';
```
"""


def load(path, default=None):
    path = pathlib.Path(path)
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def _num(value):
    from . import collect_monitoring as cm
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return cm.norm_num(value)


def _text(value):
    if value is None:
        return None
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text.replace(".", ",")
    return str(value)


def _slug(text, limit=48):
    text = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "-", str(text or "")).strip("-")
    return (text.lower()[:limit] or "x")


def _obs(**kwargs):
    row = {field: None for field in FIELDS}
    row.update(kwargs)
    row["is_iubip"] = bool(row.get("is_iubip"))
    for key in ("release", "data_year", "year"):
        if row.get(key) is not None:
            row[key] = int(row[key])
    return row


def _vo_info(card):
    out = []
    for key, value in (card.get("info") or {}).items():
        out.append(_obs(
            obs_id=f"vo:2025:I:info.{_slug(key)}",
            level="VO", release=2025, data_year=2024, scheme="vo",
            org_id=IUBIP_ID, org_name=card["vuz"]["name"], is_iubip=True,
            section="I", block="Общие сведения",
            code=f"info.{_slug(key)}", title=key, unit=None, kind="profile",
            year=2025, value_num=_num(value), value_text=value,
            source_url=VO_URL, source_file=VO_SOURCE_FILE,
        ))
    return out


def _vo_contingent(card):
    out = []
    names = {
        "total": ("Общая численность студентов", "чел."),
        "full_time": ("В том числе очной формы", "чел."),
        "subject_share": ("Доля от контингента субъекта РФ", "%"),
        "budget_share": ("За счёт бюджетных ассигнований", "%"),
        "paid_share": ("По договорам о платных услугах", "%"),
    }
    for key, value in (card.get("contingent") or {}).items():
        title, unit = names.get(key, (key, None))
        out.append(_obs(
            obs_id=f"vo:2025:IV:contingent.{key}",
            level="VO", release=2025, data_year=2024, scheme="vo",
            org_id=IUBIP_ID, org_name=card["vuz"]["name"], is_iubip=True,
            section="IV", block="Роль организации в системе подготовки кадров",
            code=f"contingent.{key}", title=title, unit=unit, kind="count",
            year=2025, value_num=_num(value), value_text=_text(value),
            source_url=VO_URL, source_file=VO_SOURCE_FILE,
        ))
    for key, value in (card.get("region") or {}).items():
        titles = {
            "organizations": ("Организаций высшего образования в регионе", "ед."),
            "students": ("Студентов в регионе", "чел."),
            "city_organizations": ("Организаций в городе", "ед."),
        }
        title, unit = titles.get(key, (key, None))
        out.append(_obs(
            obs_id=f"vo:2025:IV:region.{key}",
            level="VO", release=2025, data_year=2024, scheme="vo",
            org_id=IUBIP_ID, org_name=card["vuz"]["name"], is_iubip=True,
            section="IV", block="Роль организации в системе подготовки кадров",
            code=f"region.{key}", title=title, unit=unit, kind="count",
            year=2025, value_num=_num(value), value_text=_text(value),
            source_url=VO_URL, source_file=VO_SOURCE_FILE,
        ))
    return out


def _vo_branches(card):
    out = []
    for item in card.get("branches") or []:
        name = item.get("name") or ""
        for metric, unit in (("subject_share", "%"), ("contingent", "чел.")):
            if item.get(metric) is None:
                continue
            out.append(_obs(
                obs_id=f"vo:2025:IV:branch.{_slug(name)}.{metric}",
                level="VO", release=2025, data_year=2024, scheme="vo",
                org_id=IUBIP_ID, org_name=card["vuz"]["name"], is_iubip=True,
                section="IV", block="Распределение контингента по отраслям наук",
                code=f"branch.{_slug(name)}.{metric}", title=name, unit=unit,
                kind="share" if metric == "subject_share" else "count",
                year=2025, value_num=_num(item.get(metric)),
                value_text=_text(item.get(metric)),
                source_url=VO_URL, source_file=VO_SOURCE_FILE,
                extra={"metric": metric},
            ))
    return out


def _vo_ugns(card):
    out = []
    metrics = [
        ("contingent", "Контингент", "чел.", "count"),
        ("share_org", "Доля в организации", "%", "share"),
        ("share_region", "Доля в регионе", "%", "share"),
        ("vuz_count", "Вузов в регионе", "ед.", "count"),
        ("branch_count", "Филиалов в регионе", "ед.", "count"),
    ]
    for item in card.get("ugns") or []:
        code = item.get("code") or ""
        for key, title, unit, kind in metrics:
            if item.get(key) is None:
                continue
            out.append(_obs(
                obs_id=f"vo:2025:IV:ugn.{code}.{key}",
                level="VO", release=2025, data_year=2024, scheme="vo",
                org_id=IUBIP_ID, org_name=card["vuz"]["name"], is_iubip=True,
                section="IV", block="Реализуемые УГН(С)",
                code=code, title=item.get("name") or code, unit=unit, kind=kind,
                year=2025, value_num=_num(item.get(key)),
                value_text=_text(item.get(key)),
                source_url=VO_URL, source_file=VO_SOURCE_FILE,
                extra={"metric": key},
            ))
    return out


def _vo_e(monitoring):
    """E-показатели ИУБиП: значения по годам, места, медианы."""
    out = []
    axes = monitoring.get("e_axes") or monitoring.get("axes") or []
    units = {axis["code"]: axis.get("unit") for axis in axes}
    labels = {axis["code"]: axis.get("label") for axis in axes}
    for release in ("2024", "2025"):
        year_data = (monitoring.get("years") or {}).get(release) or {}
        release_int = int(release)
        for code, ind in (year_data.get("indicators") or {}).items():
            if code not in units:
                # E.7 встречается только в региональной таблице и не входит
                # в опубликованный набор выпуска; без названия и единицы
                # он был бы мусором, а не фактом.
                continue
            medians = ind.get("medians") or {}
            latest = ind.get("year")
            values = ind.get("values") or {}
            for year, value in values.items():
                is_latest = str(year) == str(latest)
                prev = values.get(str(int(year) - 1))
                out.append(_obs(
                    obs_id=f"vo:{release}:II:{code}:{year}",
                    level="VO", release=release_int,
                    data_year=release_int - 1, scheme="vo",
                    org_id=IUBIP_ID, org_name=ind.get("org") or IUBIP_NAME,
                    is_iubip=True, section="II",
                    block="Показатели мониторинга эффективности",
                    code=code, title=labels.get(code) or ind.get("label") or code,
                    unit=units.get(code) or ind.get("unit"), kind="value",
                    year=int(year), value_num=_num(value), value_text=_text(value),
                    place=ind.get("rank") if is_latest else None,
                    place_total=ind.get("total") if is_latest else None,
                    percentile=ind.get("percentile") if is_latest else None,
                    median_private=None,
                    median_region=medians.get("region") if is_latest else None,
                    median_rf=medians.get("rf") if is_latest else None,
                    median_department=medians.get("department") if is_latest else None,
                    median_org=ind.get("median") if is_latest else None,
                    prev_value=_num(prev), delta=ind.get("delta_pct") if is_latest else None,
                    source_url=VO_URL, source_file=MONITORING_JSON.name,
                    extra={"best": ind.get("best")} if is_latest else None,
                ))
    return out


def _vo_region_url(release):
    return (f"https://monitoring.miccedu.ru/iam/{release}/_vpo/"
            "material.php?type=2&id=10703")


def _vo_region_e(region, monitoring):
    """E-показатели всех организаций региона; места считаются по значениям."""
    out = []
    axes = monitoring.get("e_axes") or monitoring.get("axes") or []
    units = {axis["code"]: axis.get("unit") for axis in axes}
    labels = {axis["code"]: axis.get("label") for axis in axes}
    for release in ("2024", "2025"):
        orgs = region.get(release) or []
        release_int = int(release)
        by_code_year = {}
        for org in orgs:
            for code, ind in (org.get("indicators") or {}).items():
                for year, value in (ind.get("values") or {}).items():
                    if value is not None:
                        by_code_year.setdefault((code, year), []).append(float(value))
        for org in orgs:
            org_id = str(org.get("id"))
            if org_id == IUBIP_ID:
                continue  # ИУБиП уже добавлен из monitoring.json
            for code, ind in (org.get("indicators") or {}).items():
                if code not in units:
                    continue
                medians = ind.get("medians") or {}
                values = ind.get("values") or {}
                latest_year = (max(values, key=lambda item: int(item))
                               if values else None)
                for year, value in values.items():
                    is_latest = str(year) == str(latest_year)
                    peers = by_code_year.get((code, year)) or []
                    place = total = percentile = median_org = None
                    if peers and value is not None:
                        total = len(peers)
                        place = 1 + sum(1 for peer in peers if peer > value)
                        if total > 1:
                            percentile = round(100 * (total - place) / (total - 1))
                        ordered = sorted(peers)
                        mid = total // 2
                        median_org = (ordered[mid] if total % 2
                                      else round((ordered[mid - 1] + ordered[mid]) / 2, 2))
                    out.append(_obs(
                        obs_id=f"vo:{release}:II:{code}:{year}:{org_id}",
                        level="VO", release=release_int,
                        data_year=release_int - 1, scheme="vo",
                        org_id=org_id, org_name=org.get("name") or org_id,
                        is_iubip=False, section="II",
                        block="Показатели мониторинга эффективности",
                        code=code, title=labels.get(code) or ind.get("name") or code,
                        unit=units.get(code), kind="value", year=int(year),
                        value_num=_num(value), value_text=_text(value),
                        place=place, place_total=total, percentile=percentile,
                        median_region=(medians.get("region")
                                       if is_latest else None),
                        median_rf=medians.get("rf") if is_latest else None,
                        median_department=(medians.get("department")
                                           if is_latest else None),
                        median_org=median_org,
                        source_url=_vo_region_url(release),
                        source_file=VO_REGION.name,
                    ))
    return out


def _vo_directions(directions):
    out = []
    for section in directions.get("sections") or []:
        for ind in section.get("indicators") or []:
            out.append(_obs(
                obs_id=f"vo:2025:III:{ind['code']}",
                level="VO", release=2025, data_year=2024, scheme="vo",
                org_id=IUBIP_ID, org_name=directions["vuz"]["name"],
                is_iubip=True, section="III", block=section.get("title"),
                code=ind["code"], title=ind.get("title"),
                unit=ind.get("unit"), kind="value", year=2025,
                value_num=_num(ind.get("value")),
                value_text=ind.get("raw") or _text(ind.get("value")),
                median_private=ind.get("median_private"),
                median_region=ind.get("median_region"),
                source_url=VO_URL, source_file=VO_DIRECTIONS.name,
                extra={
                    "ratio_private": ind.get("median_private_ratio"),
                    "ratio_region": ind.get("median_region_ratio"),
                    "capped_private": ind.get("median_private_capped"),
                    "capped_region": ind.get("median_region_capped"),
                    "comparable_private": ind.get("median_private_comparable"),
                    "comparable_region": ind.get("median_region_comparable"),
                },
            ))
    return out


def _vo_details(card, detail):
    """Раздел V: 56 характеристик за 2025 и 2024 годы."""
    out = []
    additional = card.get("additional") or []
    by_title = {}
    for index, item in enumerate(additional, 1):
        code = f"V.{index:02d}"
        by_title[item.get("title")] = (code, item)
        out.append(_obs(
            obs_id=f"vo:2025:V:{code}",
            level="VO", release=2025, data_year=2024, scheme="vo",
            org_id=IUBIP_ID, org_name=card["vuz"]["name"], is_iubip=True,
            section="V", block=item.get("section") or "Дополнительные характеристики",
            code=code, title=item.get("title"), unit=item.get("unit"),
            kind="value", year=2025,
            value_num=_num(item.get("value")),
            value_text=item.get("raw") or _text(item.get("value")),
            source_url=VO_URL, source_file=VO_CARD.name,
            extra={"num": item.get("num"), "sub": item.get("sub")},
        ))
    for index, item in enumerate((detail.get("2024") or []), 1):
        title = item.get("title")
        code, parent = by_title.get(title, (f"V.24.{index:02d}", {}))
        out.append(_obs(
            obs_id=f"vo:2024:V:{code}",
            level="VO", release=2024, data_year=2023, scheme="vo",
            org_id=IUBIP_ID, org_name=card["vuz"]["name"], is_iubip=True,
            section="V", block=parent.get("section") or "Дополнительные характеристики",
            code=code, title=title, unit=item.get("unit"), kind="value",
            year=2024, value_num=_num(item.get("value")),
            value_text=item.get("value"),
            source_url=VO_URL_2024,
            source_file=VO_DETAIL.name,
            extra={"matched_2025": bool(parent)},
        ))
    return out


def _spo_observations(spo):
    """СПО: сведения, контингент, УГПС и 20 показателей."""
    from . import monitoring as mon
    out = []
    years = spo.get("years") or {}
    release_data = years.get("2025") or {}
    info = release_data.get("info") or {}
    for key, value in info.items():
        out.append(_obs(
            obs_id=f"spo:2025:I:info.{_slug(key)}",
            level="SPO", release=2025, data_year=2024, scheme="spo",
            org_id=IUBIP_ID, org_name="Южный университет (ИУБиП), СПО",
            is_iubip=True, section="I", block="Общие сведения",
            code=f"info.{_slug(key)}", title=key, unit=None, kind="profile",
            year=2025, value_num=_num(value), value_text=value,
            source_url=SPO_URL, source_file=SPO_JSON.name,
        ))
    region = release_data.get("region") or {}
    city = release_data.get("city") or {}
    contingent = release_data.get("contingent") or {}
    for key, title, unit in (
        ("organizations", "Организаций СПО в регионе", "ед."),
        ("students", "Студентов СПО в регионе", "чел."),
    ):
        if region.get(key) is not None:
            out.append(_obs(
                obs_id=f"spo:2025:IV:region.{key}",
                level="SPO", release=2025, data_year=2024, scheme="spo",
                org_id=IUBIP_ID, org_name="Южный университет (ИУБиП), СПО",
                is_iubip=True, section="IV",
                block="Роль организации в системе подготовки кадров",
                code=f"region.{key}", title=title, unit=unit, kind="count",
                year=2025, value_num=_num(region.get(key)),
                value_text=_text(region.get(key)),
                source_url=SPO_URL, source_file=SPO_JSON.name,
            ))
    if city.get("organizations") is not None:
        out.append(_obs(
            obs_id="spo:2025:IV:city.organizations",
            level="SPO", release=2025, data_year=2024, scheme="spo",
            org_id=IUBIP_ID, org_name="Южный университет (ИУБиП), СПО",
            is_iubip=True, section="IV",
            block="Роль организации в системе подготовки кадров",
            code="city.organizations", title="Организаций СПО в городе",
            unit="ед.", kind="count", year=2025,
            value_num=_num(city.get("organizations")),
            value_text=_text(city.get("organizations")),
            source_url=SPO_URL, source_file=SPO_JSON.name,
        ))
    names = {
        "total": ("Обучается студентов", "чел."),
        "full_time": ("В том числе очной формы", "чел."),
        "subject_share": ("Доля от контингента субъекта РФ", "%"),
        "budget_share": ("За счёт бюджетных ассигнований", "%"),
        "paid_share": ("По договорам о платных услугах", "%"),
    }
    for key, value in contingent.items():
        title, unit = names.get(key, (key, None))
        out.append(_obs(
            obs_id=f"spo:2025:IV:contingent.{key}",
            level="SPO", release=2025, data_year=2024, scheme="spo",
            org_id=IUBIP_ID, org_name="Южный университет (ИУБиП), СПО",
            is_iubip=True, section="IV",
            block="Роль организации в системе подготовки кадров",
            code=f"contingent.{key}", title=title, unit=unit, kind="count",
            year=2025, value_num=_num(value), value_text=_text(value),
            source_url=SPO_URL, source_file=SPO_JSON.name,
        ))
    for item in release_data.get("branches") or []:
        name = item.get("name") or ""
        for metric, unit in (("subject_share", "%"), ("contingent", "чел.")):
            if item.get(metric) is None:
                continue
            out.append(_obs(
                obs_id=f"spo:2025:IV:branch.{_slug(name)}.{metric}",
                level="SPO", release=2025, data_year=2024, scheme="spo",
                org_id=IUBIP_ID, org_name="Южный университет (ИУБиП), СПО",
                is_iubip=True, section="IV",
                block="Распределение контингента по отраслям наук",
                code=f"branch.{_slug(name)}.{metric}", title=name, unit=unit,
                kind="share" if metric == "subject_share" else "count",
                year=2025, value_num=_num(item.get(metric)),
                value_text=_text(item.get(metric)),
                source_url=SPO_URL, source_file=SPO_JSON.name,
                extra={"metric": metric},
            ))
    for item in release_data.get("ugps") or []:
        code = item.get("code") or ""
        for key, title, unit in (
            ("city", "Организаций в городе", "ед."),
            ("region", "Организаций в регионе", "ед."),
            ("rf", "Организаций в РФ", "ед."),
        ):
            if item.get(key) is None:
                continue
            out.append(_obs(
                obs_id=f"spo:2025:IV:ugps.{code}.{key}",
                level="SPO", release=2025, data_year=2024, scheme="spo",
                org_id=IUBIP_ID, org_name="Южный университет (ИУБиП), СПО",
                is_iubip=True, section="IV",
                block="Укрупнённые группы профессий и специальностей (УГПС)",
                code=code, title=item.get("name") or code, unit=unit,
                kind="count", year=2025, value_num=_num(item.get(key)),
                value_text=_text(item.get(key)),
                source_url=SPO_URL, source_file=SPO_JSON.name,
                extra={"metric": key},
            ))
    sections = {item["code"]: item["title"] for item in mon.SPO_SECTIONS}
    previous = (years.get("2024") or {}).get("indicators") or {}
    for code, ind in (release_data.get("indicators") or {}).items():
        if str(code).startswith("old-"):
            continue
        section = str(code).split(".")[0]
        prev = previous.get(code) or previous.get(mon.SPO_PREV_CODE.get(code))
        prev_org = (prev or {}).get("org")
        delta = ind.get("delta")
        if delta is None and prev_org not in (None, 0):
            if ind.get("org") is not None:
                delta = round((ind["org"] - prev_org) / abs(prev_org) * 100, 1)
        out.append(_obs(
            obs_id=f"spo:2025:{section}:{code}",
            level="SPO", release=2025, data_year=2024, scheme="spo",
            org_id=IUBIP_ID, org_name="Южный университет (ИУБиП), СПО",
            is_iubip=True, section=section, block=sections.get(section),
            code=code, title=ind.get("title"), unit=_spo_unit(code),
            kind="value", year=2025, value_num=_num(ind.get("org")),
            value_text=_text(ind.get("org")),
            place=ind.get("rank"), place_total=ind.get("total"),
            percentile=ind.get("percentile"), median_org=ind.get("median"),
            subject_value=_num(ind.get("region")), rf_value=_num(ind.get("rf")),
            prev_value=_num(prev_org), delta=delta,
            source_url=SPO_URL, source_file=SPO_JSON.name,
            extra={"mapped_prev": bool(prev) and code not in previous},
        ))
    old = (years.get("2024") or {}).get("indicators") or {}
    old_to_new = {old_code: new_code
                  for new_code, old_code in mon.SPO_PREV_CODE.items()}
    for code, ind in old.items():
        is_old = str(code).startswith("old-")
        section = str(code).split("-")[-1].split(".")[0]
        unit = _spo_unit(old_to_new.get(code) if is_old else str(code))
        out.append(_obs(
            obs_id=f"spo:2024:{section}:{code}",
            level="SPO", release=2024, data_year=2023,
            scheme="spo-2024" if is_old else "spo",
            org_id=IUBIP_ID, org_name="Южный университет (ИУБиП), СПО",
            is_iubip=True, section=section, block=sections.get(section),
            code=code, title=ind.get("title"), unit=unit,
            kind="value", year=2024, value_num=_num(ind.get("org")),
            value_text=_text(ind.get("org")),
            subject_value=_num(ind.get("region")), rf_value=_num(ind.get("rf")),
            source_url=SPO_URL_2024, source_file=SPO_JSON.name,
            extra={"scheme": "spo-2024"} if is_old else {"scheme": "spo"},
        ))
    return out


def _spo_unit(code):
    units = {
        "1.1": "чел.", "1.2": "%", "1.3": "балл", "1.4": "%",
        "2.1": "%", "2.2": "%", "3.1": "%", "3.2": "%", "3.3": "%",
        "4.1": "%", "4.2": "%", "4.3": "%", "5.1": "%", "5.2": "%",
        "6.1": "%", "6.2": "%", "7.1": "%", "7.2": "%", "7.3": "чел.",
        "7.4": "%",
    }
    return units.get(code)


def _organizations(card, region, spo, spo_region):
    out = {}
    for release in ("2024", "2025"):
        for org in region.get(release) or []:
            org_id = str(org.get("id"))
            key = ("VO", org_id)
            if key not in out:
                out[key] = {
                    "level": "VO", "org_id": org_id, "name": org.get("name"),
                    "is_iubip": org_id == IUBIP_ID,
                    "region": "Ростовская область", "profile": {},
                }
    for org in spo_region.get("organizations") or []:
        org_id = str(org.get("id"))
        key = ("SPO", org_id)
        if key not in out:
            out[key] = {
                "level": "SPO", "org_id": org_id, "name": org.get("name"),
                "is_iubip": org_id == IUBIP_ID,
                "region": "Ростовская область", "profile": {},
            }
    if ("VO", IUBIP_ID) not in out:
        out[("VO", IUBIP_ID)] = {
            "level": "VO", "org_id": IUBIP_ID, "name": card["vuz"]["name"],
            "is_iubip": True, "region": "Ростовская область", "profile": {},
        }
    if ("SPO", IUBIP_ID) not in out:
        out[("SPO", IUBIP_ID)] = {
            "level": "SPO", "org_id": IUBIP_ID,
            "name": "Южный университет (ИУБиП), СПО",
            "is_iubip": True, "region": "Ростовская область", "profile": {},
        }
    out[("VO", IUBIP_ID)]["profile"] = card.get("info") or {}
    out[("SPO", IUBIP_ID)]["profile"] = ((spo.get("years") or {}).get("2025")
                                        or {}).get("info") or {}
    return sorted(out.values(), key=lambda item: (item["level"], item["org_id"]))


def _indicators(observations):
    out = {}
    for row in observations:
        if (row.get("kind") != "value"
                and not str(row.get("code") or "").startswith("info.")):
            continue
        key = (row["level"], row.get("scheme") or "", row.get("code") or "")
        previous = out.get(key)
        if previous and (previous.get("release") or 0) >= (row.get("release") or 0):
            continue
        out[key] = {
            "level": row["level"], "scheme": row.get("scheme"),
            "section": row.get("section"), "block": row.get("block"),
            "code": row.get("code"), "title": row.get("title"),
            "unit": row.get("unit"), "kind": row.get("kind"),
            "release": row.get("release"),
        }
    return sorted(out.values(), key=lambda item: (item["level"], item["scheme"] or "",
                                                  str(item["code"])))


def _cards(card, directions, detail, monitoring, spo):
    spo_year = (spo.get("years") or {}).get("2025") or {}
    return {
        "vo-2025": {
            "source": VO_URL, "release": 2025, "data_year": 2024,
            "info": card.get("info"), "contingent": card.get("contingent"),
            "region": card.get("region"), "branches": card.get("branches"),
            "ugns": card.get("ugns"), "e_table": card.get("e_table"),
            "directions": directions.get("sections"), "details": card.get("additional"),
        },
        "vo-2024": {
            "source": VO_URL_2024,
            "release": 2024, "data_year": 2023,
            "e_table": ((monitoring.get("years") or {}).get("2024") or {}),
            "details": (detail.get("2024") or []),
        },
        "spo-2025": {
            "source": SPO_URL, "release": 2025, "data_year": 2024,
            "info": spo_year.get("info"), "region": spo_year.get("region"),
            "city": spo_year.get("city"), "contingent": spo_year.get("contingent"),
            "branches": spo_year.get("branches"), "ugps": spo_year.get("ugps"),
            "indicators": spo_year.get("indicators"),
        },
        "spo-2024": {
            "source": SPO_URL_2024,
            "release": 2024, "data_year": 2023,
            "indicators": (spo.get("years") or {}).get("2024", {}).get("indicators"),
        },
    }


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


FIELD_TYPES = {
    "obs_id": "TEXT", "level": "TEXT", "release": "INTEGER",
    "data_year": "INTEGER", "scheme": "TEXT", "org_id": "TEXT",
    "org_name": "TEXT", "is_iubip": "INTEGER", "section": "TEXT",
    "block": "TEXT", "code": "TEXT", "title": "TEXT", "unit": "TEXT",
    "kind": "TEXT", "year": "INTEGER", "value_num": "REAL",
    "value_text": "TEXT", "place": "INTEGER", "place_total": "INTEGER",
    "percentile": "INTEGER", "median_private": "REAL",
    "median_region": "REAL", "median_rf": "REAL",
    "median_department": "REAL", "median_org": "REAL",
    "subject_value": "REAL", "rf_value": "REAL", "prev_value": "REAL",
    "delta": "REAL", "source_url": "TEXT", "source_file": "TEXT",
    "extra": "TEXT",
}


def _make_sqlite(path, observations, organizations, indicators):
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=OFF")
    columns = ", ".join(f"{field} {FIELD_TYPES[field]}" for field in FIELDS)
    conn.execute(f"CREATE TABLE observation ({columns}, PRIMARY KEY (obs_id))")
    conn.execute("CREATE TABLE org (level TEXT, org_id TEXT, name TEXT, "
                 "is_iubip INTEGER, region TEXT, profile TEXT, "
                 "PRIMARY KEY (level, org_id))")
    conn.execute("CREATE TABLE indicator (level TEXT, scheme TEXT, section TEXT, "
                 "block TEXT, code TEXT, title TEXT, unit TEXT, kind TEXT, "
                 "PRIMARY KEY (level, scheme, code))")
    conn.executemany(
        f"INSERT INTO observation ({', '.join(FIELDS)}) "
        f"VALUES ({', '.join('?' for _ in FIELDS)})",
        [[json.dumps(row.get(field), ensure_ascii=False)
          if field == "extra" and row.get(field) is not None else row.get(field)
          for field in FIELDS] for row in observations])
    conn.executemany(
        "INSERT INTO org VALUES (?,?,?,?,?,?)",
        [[item["level"], item["org_id"], item["name"], int(item["is_iubip"]),
          item.get("region"), json.dumps(item.get("profile") or {}, ensure_ascii=False)]
         for item in organizations])
    conn.executemany(
        "INSERT INTO indicator VALUES (?,?,?,?,?,?,?,?)",
        [[item["level"], item.get("scheme"), item.get("section"), item.get("block"),
          item.get("code"), item.get("title"), item.get("unit"), item.get("kind")]
         for item in indicators])
    conn.execute("CREATE INDEX idx_obs_lookup ON observation (level, release, code)")
    conn.execute("CREATE INDEX idx_obs_org ON observation (level, org_id, code)")
    conn.execute("CREATE INDEX idx_obs_year ON observation (level, release, year)")
    conn.commit()
    conn.close()


def build(out_dir=OUT, verbose=True):
    out_dir = pathlib.Path(out_dir)
    card = load(VO_CARD)
    directions = load(VO_DIRECTIONS)
    detail = load(VO_DETAIL)
    region = load(VO_REGION)
    monitoring = load(MONITORING_JSON)
    spo = load(SPO_JSON)
    spo_region = load(SPO_REGION)
    missing = [name for name, value in (
        ("iubip-card.json", card), ("iubip-directions.json", directions),
        ("iubip-detail.json", detail), ("region_indicators.json", region),
        ("monitoring.json", monitoring), ("spo.json", spo),
        ("spo_region.json", spo_region)) if not value]
    if missing:
        raise RuntimeError("нет исходных данных: " + ", ".join(missing))

    observations = []
    observations += _vo_info(card)
    observations += _vo_contingent(card)
    observations += _vo_branches(card)
    observations += _vo_ugns(card)
    observations += _vo_e(monitoring)
    observations += _vo_region_e(region, monitoring)
    observations += _vo_directions(directions)
    observations += _vo_details(card, detail)
    observations += _spo_observations(spo)

    seen = set()
    unique = []
    for row in observations:
        if row["obs_id"] in seen:
            raise RuntimeError("повтор obs_id: " + row["obs_id"])
        seen.add(row["obs_id"])
        unique.append(row)
    observations = sorted(unique, key=lambda row: (
        row["level"], row["release"] or 0, str(row["section"]), str(row["code"]),
        row["org_id"] or "", row["year"] or 0, row["kind"] or ""))

    organizations = _organizations(card, region, spo, spo_region)
    indicators = _indicators(observations)
    cards = _cards(card, directions, detail, monitoring, spo)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cards").mkdir(exist_ok=True)
    observations_path = out_dir / "observations.jsonl"
    observations_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in observations) + "\n",
        encoding="utf-8")
    _write_json(out_dir / "organizations.json", organizations)
    _write_json(out_dir / "indicators.json", indicators)
    _write_json(out_dir / "schema.json", SCHEMA)
    (out_dir / "README.md").write_text(README, encoding="utf-8")
    for name, payload in cards.items():
        _write_json(out_dir / "cards" / f"{name}.json", payload)

    with (out_dir / "observations.csv").open("w", encoding="utf-8-sig",
                                             newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in observations:
            item = dict(row)
            if item.get("extra") is not None:
                item["extra"] = json.dumps(item["extra"], ensure_ascii=False)
            writer.writerow(item)

    db_path = out_dir / "monitoring.sqlite"
    _make_sqlite(db_path, observations, organizations, indicators)

    counts = {}
    for row in observations:
        key = f"{row['level']}-{row['release']}"
        counts[key] = counts.get(key, 0) + 1
    manifest = {
        "schema_version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sources": [
            {"id": "vo-2025", "level": "VO", "release": 2025, "data_year": 2024,
             "url": VO_URL, "release_date": "2026-02-03"},
            {"id": "vo-2024", "level": "VO", "release": 2024, "data_year": 2023,
             "url": VO_URL_2024},
            {"id": "spo-2025", "level": "SPO", "release": 2025, "data_year": 2024,
             "url": SPO_URL},
            {"id": "spo-2024", "level": "SPO", "release": 2024, "data_year": 2023,
             "url": SPO_URL_2024},
        ],
        "counts": {
            "observations": len(observations),
            "organizations": len(organizations),
            "indicators": len(indicators),
            "by_release": counts,
        },
        "checksums": {
            "observations.jsonl": _sha256(observations_path),
            "observations.csv": _sha256(out_dir / "observations.csv"),
            "organizations.json": _sha256(out_dir / "organizations.json"),
            "indicators.json": _sha256(out_dir / "indicators.json"),
            "monitoring.sqlite": _sha256(db_path),
        },
    }
    _write_json(out_dir / "manifest.json", manifest)
    if verbose:
        print(f"собрано: {len(observations)} фактов, "
              f"{len(organizations)} организаций, {len(indicators)} показателей")
        print(f"каталог: {out_dir}")
    return manifest


def connect(path=None):
    path = pathlib.Path(path or OUT)
    if path.is_dir():
        path = path / "monitoring.sqlite"
    if not path.exists():
        raise FileNotFoundError(f"нет базы: {path}; сначала build")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def lookup(level="VO", release=2025, code=None, org_id=None, title=None,
           path=None, limit=50):
    conn = connect(path)
    conn.row_factory = sqlite3.Row
    where, params = ["level = ?", "release = ?"], [level, int(release)]
    if code:
        where.append("code = ?")
        params.append(code)
    if org_id:
        where.append("org_id = ?")
        params.append(str(org_id))
    if title:
        where.append("title LIKE ?")
        params.append(f"%{title}%")
    sql = ("SELECT code, title, unit, kind, year, value_num, value_text, place, "
           "place_total, percentile, median_private, median_region, median_rf, "
           "median_department, median_org, subject_value, rf_value, prev_value, "
           "delta, source_url FROM observation WHERE "
           + " AND ".join(where) + " ORDER BY code, year LIMIT ?")
    rows = [dict(row) for row in conn.execute(sql, params + [limit])]
    conn.close()
    return rows


STOP_WORDS = {
    "какое", "какая", "какие", "каков", "какова", "каковы", "сколько",
    "что", "известно", "значение", "показателя", "показатель", "иубип",
    "выпуске", "выпуск", "году", "год", "есть", "было", "были", "дает",
    "дают", "мониторинг", "мониторинга", "организации", "организация",
    "регион", "регионе", "области", "область", "россии", "рф", "спо",
    "среднего", "профессионального", "образования", "вуза", "вузе",
    "университет", "южный", "наш", "наша", "наши", "данные", "данных",
}

OTHER_ORG_MARKERS = (
    "юфу", "дгту", "ринх", "южный федеральный", "донской государственный",
    "ростовский государственный", "медицинский университет",
)


def _stem(word):
    """Грубая основа: «сайте» и «сайт» должны совпасть."""
    for suffix in ("иями", "ями", "ами", "ов", "ев", "ой", "ей", "ая",
                   "ые", "ие", "ый", "ий", "ом", "ем", "ах", "ях", "ам",
                   "ям", "у", "ю", "а", "я", "ы", "и", "е", "о"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[:-len(suffix)]
            break
    return word[:6]


def _tokens(text):
    words = re.findall(r"[А-Яа-яЁёA-Za-z0-9]+", str(text or "").lower())
    out = set()
    for word in words:
        if len(word) < 4 or word in STOP_WORDS:
            continue
        out.add(_stem(word))
    return out


def _explicit_code(question):
    text = str(question or "")
    match = re.search(r"\bE\.?\s*(\d)\b", text, re.IGNORECASE)
    if match:
        return f"E{match.group(1)}"
    match = re.search(r"\b(\d{2}\.\d{2}\.\d{2})\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d+\.\d+)\b", text)
    if match:
        return match.group(1)
    return None


def _match_indicator(question, level, path=None):
    tokens = _tokens(question)
    if not tokens:
        return None
    base = pathlib.Path(path or OUT)
    indicators = [item for item in load(base / "indicators.json", [])
                  if item.get("level") == level
                  and item.get("release") == 2025]
    scored = []
    for item in indicators:
        title_tokens = _tokens(item.get("title"))
        if not title_tokens:
            continue
        overlap = len(tokens & title_tokens)
        threshold = max(1, min(3, len(title_tokens) // 4))
        if overlap >= threshold:
            scored.append((overlap, item.get("code")))
    scored.sort(key=lambda item: -item[0])
    if not scored:
        return None
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # два равных кандидата — не угадываем
    return scored[0][1]


def _metric_from_question(question):
    low = str(question or "").lower()
    if "доля" in low:
        if "регион" in low or "област" in low:
            return "share_region"
        if "организац" in low:
            return "share_org"
        if "субъект" in low:
            return "subject_share"
    if "контингент" in low or "студент" in low:
        return "contingent"
    if "филиал" in low:
        return "branch_count"
    if "вуз" in low:
        return "vuz_count"
    if "город" in low:
        return "city"
    if "рф" in low or "россии" in low:
        return "rf"
    if "регион" in low or "област" in low:
        return "region"
    return None


def _rel_path(row):
    if row.get("level") == "SPO":
        return "official/monitoring/spo/2025-iubip.md"
    return {
        "I": "official/monitoring/2025-card.md",
        "II": "official/monitoring/region-2025.md",
        "III": "official/monitoring/2025-directions.md",
        "IV": "official/monitoring/2025-card.md",
        "V": "official/monitoring/2025-iubip.md",
    }.get(row.get("section"), "official/monitoring/2025-card.md")


def answer_question(question, path=None):
    """Точный ответ из store для вопроса чата. None — если не уверены."""
    text = str(question or "").strip()
    if not text:
        return None
    low = text.lower()
    if (any(marker in low for marker in OTHER_ORG_MARKERS)
            and "иубип" not in low and "южный университет" not in low):
        return None
    level = ("SPO" if re.search(r"\bСПО\b|среднего профессионального|колледж",
                                text, re.IGNORECASE) else "VO")
    code = _explicit_code(text) or _match_indicator(text, level, path=path)
    if not code:
        return None
    conn = connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM observation WHERE level=? AND release=2025 "
        "AND org_id='1971' AND code=? "
        "ORDER BY CASE kind WHEN 'value' THEN 0 WHEN 'count' THEN 1 "
        "WHEN 'share' THEN 2 ELSE 3 END, year DESC",
        (level, code))]
    conn.close()
    if not rows:
        return None
    if len(rows) > 1:
        metric = _metric_from_question(text)
        if not metric:
            return None
        rows = [row for row in rows
                if metric in str(row.get("extra") or "")]
        if len(rows) != 1:
            return None
    row = rows[0]
    unit = row.get("unit") or ""
    parts = [f"{row.get('title')} — {row.get('value_text')} {unit}".strip()]
    for label, key in (("медиана частных вузов", "median_private"),
                       ("медиана региона", "median_region"),
                       ("медиана РФ", "median_rf"),
                       ("медиана ведомства", "median_department"),
                       ("медиана по организациям", "median_org")):
        if row.get(key) is not None:
            parts.append(f"{label} — {_text(row[key])} {unit}".strip())
    if row.get("subject_value") is not None:
        parts.append(f"по субъекту РФ — {_text(row['subject_value'])} {unit}".strip())
    if row.get("rf_value") is not None:
        parts.append(f"по РФ — {_text(row['rf_value'])} {unit}".strip())
    if row.get("place") is not None:
        parts.append(f"место в регионе — {row['place']} "
                     f"из {row.get('place_total')}")
    body = "; ".join(parts)
    if not body.endswith("."):
        body += "."
    return {
        "kind": "store",
        "level": level,
        "release": 2025,
        "code": row.get("code"),
        "title": row.get("title"),
        "text": body,
        "sources": [{
            "doc_title": "Структурированная копия мониторинга",
            "rel_path": _rel_path(row),
            "quote": body,
        }],
        "formula": "точное значение из структурированной копии "
                   "monitoring.miccedu.ru",
    }


def stats(path=None):
    manifest = load(pathlib.Path(path or OUT) / "manifest.json")
    return manifest.get("counts") or {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Структурированная копия мониторинга")
    sub = ap.add_subparsers(dest="cmd", required=True)
    build_cmd = sub.add_parser("build", help="собрать пакет")
    build_cmd.add_argument("--out", default=str(OUT))
    lookup_cmd = sub.add_parser("lookup", help="найти факт")
    lookup_cmd.add_argument("--level", default="VO", choices=["VO", "SPO"])
    lookup_cmd.add_argument("--release", type=int, default=2025)
    lookup_cmd.add_argument("--code")
    lookup_cmd.add_argument("--org")
    lookup_cmd.add_argument("--title")
    lookup_cmd.add_argument("--path", default=str(OUT))
    sub.add_parser("stats", help="счётчики")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.out)
        return 0
    if args.cmd == "lookup":
        rows = lookup(args.level, args.release, args.code, args.org,
                      args.title, path=args.path)
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print(json.dumps(stats(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
