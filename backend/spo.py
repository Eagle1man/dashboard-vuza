# -*- coding: utf-8 -*-
"""Разбор карточки СПО monitoring.miccedu.ru и сборка data/spo.json.

Почему отдельный модуль. СПО — не «уменьшенная копия» ВО: другая
страница, другие 20 показателей, другая региональная таблица. Общий
код с ВО здесь был бы набором ветвлений, поэтому парсер отдельный.

Карточка СПО содержит семь таблиц `napde` с четырьмя колонками:
название, значение организации, значение по субъекту РФ, значение по
РФ. Этого достаточно для org/region/rf. Места и медианы считаются по
карточкам всех организаций региона — их собирает sync_spo.py и кладёт
в `source/spo_region.json`.
"""
import json
import pathlib
import re
import statistics

from . import collect_monitoring as cm

VUZ_ID = "1971"
REGION_ID = "10703"
SITE = "https://monitoring.miccedu.ru"

CODE_RE = re.compile(r"(\d+\.\d+)\.\s*(.+)", re.DOTALL)
LINK_RE = re.compile(
    r'<a\b[^>]*href=["\'](?:[^"\']*/)?inst\.php\?id=(\d+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def card_url(year, vuz_id=VUZ_ID):
    return f"{SITE}/iam/{year}/_spo/inst.php?id={vuz_id}"


def region_url(year, region_id=REGION_ID):
    return f"{SITE}/iam/{year}/_spo/material.php?type=2&id={region_id}"


def _cells(row):
    return [cell["t"] for cell in row]


def _table_by_id(tables, table_id):
    return next((t for t in tables if t["attrs"].get("id") == table_id), None)


def _table_by_class(tables, class_name):
    return next((t for t in tables
                 if class_name in (t["attrs"].get("class") or "")), None)


def _parse_region(tables):
    """Итоги региона: организаций и студентов СПО."""
    for table in tables:
        rows = table["rows"]
        if len(rows) < 2:
            continue
        first, second = _cells(rows[0]), _cells(rows[1])
        if (first and first[0].strip().isdigit()
                and len(first) > 1
                and "организации, реализующие программы СПО" in first[1]):
            return {
                "organizations": int(first[0]),
                "students": int(cm.norm_num(second[0]) or 0),
            }
    return {}


def _parse_city(tables):
    """Итоги города: число организаций СПО."""
    for table in tables:
        for row in table["rows"]:
            cells = _cells(row)
            if (cells and cells[0].strip().isdigit()
                    and len(cells) > 1
                    and "организаций, реализующих программы СПО" in cells[1]):
                return {"organizations": int(cells[0])}
    return {}


def _parse_contingent(tables):
    """Контингент организации, очная форма и доли бюджета/платных мест."""
    out = {}
    for table in tables:
        for row in table["rows"]:
            cells = _cells(row)
            if not cells:
                continue
            head = cells[0].lower()
            if head.startswith("обучается"):
                out["total"] = int(cm.norm_num(cells[0]) or 0)
                if len(cells) > 1:
                    match = re.search(r"\(([\d.,]+)\s*%", cells[1])
                    if match:
                        out["subject_share"] = float(
                            match.group(1).replace(",", "."))
            elif head.startswith("в т.ч."):
                out["full_time"] = int(cm.norm_num(cells[0]) or 0)
            elif head.startswith("за счет бюджетных ассигнований"):
                if len(cells) > 1:
                    out["budget_share"] = cm.norm_num(cells[1])
                if len(cells) > 3:
                    out["paid_share"] = cm.norm_num(cells[3])
    return out


def _parse_branches(tables):
    """Распределение контингента по отраслям наук."""
    table = _table_by_id(tables, "kont_by_otr")
    if not table:
        return []
    out = []
    for row in table["rows"][1:]:
        cells = _cells(row)
        if len(cells) < 3 or not cells[0]:
            continue
        out.append({
            "name": cells[0],
            "subject_share": cm.norm_num(cells[1]),
            "contingent": cm.norm_num(cells[2]),
        })
    return out


def _parse_ugps(tables):
    """УГПС, по которым организация реализует подготовку кадров."""
    table = _table_by_class(tables, "table_ugs")
    if not table:
        return []
    out = []
    for row in table["rows"]:
        cells = _cells(row)
        if len(cells) < 4:
            continue
        match = re.match(r"(\d{2}\.\d{2}\.\d{2})\s*[-–—]\s*(.+)", cells[0])
        if not match:
            continue
        out.append({
            "code": match.group(1),
            "name": match.group(2).strip(),
            "city": int(cm.norm_num(cells[1]) or 0),
            "region": int(cm.norm_num(cells[2]) or 0),
            "rf": int(cm.norm_num(cells[3]) or 0),
        })
    return out


def parse_card(html_text):
    """Карточка СПО: сведения, контингент, УГПС и 20 показателей."""
    tables = cm.parse_tables(html_text)
    info = {str(key).lower(): value
            for key, value in cm.parse_info(tables).items()}
    indicators = {}
    for table in tables:
        if "napde" not in (table["attrs"].get("class") or ""):
            continue
        for row in table["rows"]:
            cells = _cells(row)
            if len(cells) < 4:
                continue
            match = CODE_RE.match(cells[0])
            if not match:
                continue
            code = match.group(1)
            indicators[code] = {
                "title": " ".join(match.group(2).split()),
                "org": cm.norm_num(cells[1]),
                "region": cm.norm_num(cells[2]),
                "rf": cm.norm_num(cells[3]),
            }
    return {
        "info": info,
        "name": info.get("наименование образовательной организации") or "",
        "region": _parse_region(tables),
        "city": _parse_city(tables),
        "contingent": _parse_contingent(tables),
        "branches": _parse_branches(tables),
        "ugps": _parse_ugps(tables),
        "indicators": indicators,
    }


def region_ids(html_text):
    """Организации СПО региона: id и название из ссылок на карточки."""
    out, seen = [], set()
    for match in LINK_RE.finditer(html_text or ""):
        vuz_id = match.group(1)
        if vuz_id in seen:
            continue
        name = " ".join(TAG_RE.sub(" ", match.group(2)).split())
        if not name:
            continue
        seen.add(vuz_id)
        out.append({"id": vuz_id, "name": name})
    return out


def card_entry(card):
    """Строка организации для source/spo_region.json.

    Храним только значения: названия показателей берутся из карточки
    ИУБиП, а кэш нужен лишь для мест и медиан. Без названий файл в
    разы меньше.
    """
    return {
        "id": VUZ_ID,
        "name": card.get("name") or "ИУБиП",
        "indicators": {
            code: ind["org"]
            for code, ind in (card.get("indicators") or {}).items()
        },
    }


def build_release(card, organizations, previous=None):
    """Показатели выпуска: значения, места, перцентили, медианы, динамика.

    Место — соревновательное: 1 + число организаций со строго большим
    значением. При большом числе нулей это даёт одинаковое место всем
    нулям — так же считает и сайт.
    """
    previous = previous or {}
    out = {}
    for code, ind in (card.get("indicators") or {}).items():
        org_value = ind.get("org")
        values = []
        for entry in organizations:
            raw = ((entry.get("indicators") or {}).get(code))
            value = raw.get("value") if isinstance(raw, dict) else raw
            if value is not None:
                values.append(float(value))
        total = len(values)
        rank = percentile = median = None
        if org_value is not None and total:
            rank = 1 + sum(1 for value in values if value > org_value)
            percentile = (round(100 * (total - rank) / (total - 1))
                          if total > 1 else 100)
            median = round(statistics.median(values), 2)
        prev_org = (previous.get(code) or {}).get("org")
        delta = None
        if org_value is not None and prev_org not in (None, 0):
            delta = round((org_value - prev_org) / abs(prev_org) * 100, 1)
        out[code] = {
            "title": ind["title"],
            "org": org_value,
            "region": ind["region"],
            "rf": ind["rf"],
            "rank": rank,
            "total": total,
            "percentile": percentile,
            "median": median,
            "delta": delta,
        }
    return out


def write_spo_md(spo, release, path):
    """Текст СПО-карточки для RAG: значения и место в области."""
    years = sorted((spo.get("years") or {}).keys())
    previous = None
    if release in years:
        index = years.index(release)
        if index > 0:
            previous = years[index - 1]
    release_data = (spo.get("years") or {}).get(str(release)) or {}
    info = release_data.get("info") or {}
    region = release_data.get("region") or {}
    city = release_data.get("city") or {}
    contingent = release_data.get("contingent") or {}
    branches = release_data.get("branches") or []
    ugps = release_data.get("ugps") or []

    lines = [
        "# ИУБиП: среднее профессиональное образование, мониторинг СПО",
        "",
        "Источник: monitoring.miccedu.ru, карточка организации id=1971.",
        f"Выпуск {release} — самый свежий; предыдущий: {previous or '—'}.",
        "",
    ]
    if info:
        lines += ["## Общие сведения", ""]
        for key, value in info.items():
            if key and value:
                lines.append(f"- {key}: {value}")
        lines.append("")
    if region or city or contingent:
        lines += ["## Роль организации в системе подготовки кадров", ""]
        if region.get("organizations"):
            lines.append(f"- Организаций СПО в регионе: {region['organizations']}")
        if region.get("students"):
            lines.append(f"- Студентов СПО в регионе: {region['students']}")
        if city.get("organizations"):
            lines.append(f"- Организаций СПО в городе: {city['organizations']}")
        if contingent.get("total") is not None:
            lines.append(f"- Обучается студентов: {contingent['total']}")
        if contingent.get("full_time") is not None:
            lines.append(f"- В том числе очной формы: {contingent['full_time']}")
        if contingent.get("subject_share") is not None:
            lines.append(f"- Доля от контингента субъекта РФ: "
                         f"{contingent['subject_share']}%")
        if contingent.get("budget_share") is not None:
            lines.append(f"- За счёт бюджетных ассигнований: "
                         f"{contingent['budget_share']}%")
        if contingent.get("paid_share") is not None:
            lines.append(f"- По договорам о платных услугах: "
                         f"{contingent['paid_share']}%")
        if branches:
            lines += ["", "### Распределение контингента по отраслям наук", ""]
            for branch in branches:
                lines.append(
                    f"- {branch['name']}: доля в субъекте "
                    f"{branch.get('subject_share')}%, контингент "
                    f"{branch.get('contingent')} чел.")
        lines.append("")
    if ugps:
        lines += ["## Укрупнённые группы профессий и специальностей (УГПС)", ""]
        for item in ugps:
            lines.append(
                f"- {item['code']} — {item['name']}: организаций в городе "
                f"{item.get('city')}, в регионе {item.get('region')}, "
                f"в РФ {item.get('rf')}")
        lines.append("")
    lines += ["## Показатели организации, области и РФ", ""]
    indicators = (spo.get("years", {}).get(str(release), {})
                  .get("indicators") or {})
    for code, ind in indicators.items():
        if str(code).startswith("old-"):
            continue
        line = (f"- {ind['title']} — ИУБиП: {ind['org']}, "
                f"Ростовская область: {ind['region']}, РФ: {ind['rf']}"
                f"; место в области: {ind['rank']} из {ind['total']}")
        if previous:
            prev = ((spo["years"][previous].get("indicators") or {})
                    .get(code) or {})
            if prev.get("org") is not None:
                line += f"; в выпуске {previous}: {prev['org']}"
        lines.append(line)
    cm._write_text_atomic(path, "\n".join(lines) + "\n")


def write_json(path, data):
    cm._write_text_atomic(
        path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
