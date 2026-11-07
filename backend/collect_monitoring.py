# -*- coding: utf-8 -*-
"""Сбор карточки вуза с monitoring.miccedu.ru.

Зачем отдельный сборщик. Раздел III «Результаты по направлениям
деятельности» на сайте есть, а в проекте его не было: данные лежали
только в карточке 2025 года как текст раздела V. Раздел III — это
57 показателей в шести таблицах, и без него дашборд показывает не
всю картину мониторинга.

Что делает:
  1. Забирает страницу inst.php?id=1971 (или читает сохранённый HTML).
  2. Разбирает разделы I (общие сведения), III (направления), IV
     (роль в подготовке кадров) и V (дополнительные характеристики).
  3. Пишет:
       knowledge/official/monitoring/source/iubip-directions.json
       knowledge/official/monitoring/source/iubip-card.json
       knowledge/official/monitoring/2025-directions.md
       knowledge/official/monitoring/2025-card.md
     и обновляет 2025-ю часть iubip-detail.json.

Сравнение с медианами. На сайте медианы раздела III показаны не
числами, а графиком отношения «значение вуза / медиана»: 1 — ровно
медиана, 2 — вдвое выше (шкала обрезана сверху), 0 — сравнения нет
(значение нулевое или медиана не заполнена). Поэтому точную медиану
удаётся восстановить только при отношении строго между 0 и 2; при
двойке честно пишем «≥ 2× медианы», а не выдумываем число.

    python -m backend.collect_monitoring
    python -m backend.collect_monitoring --html путь\\к\\странице.html

Сеть не является зависимостью сборки дашборда: JSON-файлы лежат в
репозитории, а сборщик запускается вручную при обновлении выпуска.
"""
import argparse
import json
import pathlib
import re
import sys
import urllib.request
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KB = ROOT / "knowledge" / "official" / "monitoring"
SOURCE = KB / "source"
URL = "https://monitoring.miccedu.ru/iam/2025/_vpo/inst.php?id=1971"

SECTION_TITLES = {
    1: "Образовательная деятельность",
    2: "Научно-исследовательская деятельность",
    3: "Международная деятельность",
    4: "Финансово-экономическая деятельность",
    5: "Инфраструктура",
    6: "Кадровый состав",
}

# Единицы для E-показателей: на сайте они не подписаны в таблице.
E_UNITS = {
    "E1": "балл",
    "E2": "тыс. ₽ на НПР",
    "E3": "%",
    "E4": "тыс. ₽ на НПР",
    "E5": "%",
    "E8": "на 100 студентов",
}

NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def norm_num(text):
    """«1 350», «60,36», «–5,5%», «да» -> float | None."""
    if text is None:
        return None
    raw = str(text).replace("\xa0", " ").replace("\u2013", "-").replace("\u2014", "-")
    m = NUM_RE.search(raw.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


class TableParser(HTMLParser):
    """Собирает таблицы как строки ячеек; терпит незакрытые <tr>."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self.stack = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.stack.append({"attrs": a, "rows": []})
        elif tag == "tr" and self.stack:
            self._flush_row()
            self.row = []
        elif tag in ("td", "th") and self.stack and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def _flush_row(self):
        if self.stack and self.row is not None:
            if self.row:
                self.stack[-1]["rows"].append(self.row)
            self.row = None

    def handle_endtag(self, tag):
        if tag == "table" and self.stack:
            self._flush_row()
            t = self.stack.pop()
            if not self.stack:
                self.tables.append(t)
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append({"t": clean("".join(self.cell)), "h": tag == "th"})
            self.cell = None
        elif tag == "tr":
            self._flush_row()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def fetch_html(url=URL):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
        charset = "utf-8"
        ctype = resp.headers.get("Content-Type") or ""
        m = re.search(r"charset=([\w-]+)", ctype, re.IGNORECASE)
        if m:
            charset = m.group(1)
    try:
        return data.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return data.decode("cp1251", errors="replace")


def parse_tables(text):
    p = TableParser()
    p.feed(text)
    return p.tables


def parse_info(tables):
    """Раздел I: общие сведения."""
    table = next((t for t in tables if t["attrs"].get("id") == "info"), None)
    out = {}
    if not table:
        return out
    for row in table["rows"]:
        cells = [c["t"] for c in row]
        if len(cells) >= 2:
            key = cells[0].rstrip(":")
            value = cells[1].replace("на карте", "").strip()
            out[key] = value
    return out


def parse_e_table(tables):
    """Раздел II: E-показатели, 5 лет + динамика + медианы."""
    table = next((t for t in tables if t["attrs"].get("id") == "result"), None)
    if not table:
        return {"years": [], "indicators": []}
    years = ["2021", "2022", "2023", "2024", "2025"]
    indicators = []
    for row in table["rows"]:
        cells = [c["t"] for c in row]
        if len(cells) < 8:
            continue
        m = re.match(r"(E\.\d+)\.\s*(.+)", cells[0])
        if not m:
            continue
        code = "E" + m.group(1).split(".", 1)[1]
        # Название раздела и расшифровка склеены без разделителя:
        # «Образовательная деятельностьСредний балл ЕГЭ…». Отрезаем
        # известный заголовок раздела, чтобы в карточке осталась суть.
        title = m.group(2)
        for prefix in (
            "Образовательная деятельность",
            "Научно-исследовательская деятельность",
            "Международная деятельность",
            "Финансово-экономическая деятельность",
            "Дополнительный показатель",
        ):
            if title.startswith(prefix):
                title = title[len(prefix):].strip()
                break
        value_raw = cells[5]
        latest = value_raw.split("|")[0]
        delta = None
        if "|" in value_raw:
            dm = re.search(r"([+\-\u2013\u2014]?\s*\d+(?:[.,]\d+)?)\s*%", value_raw)
            if dm:
                delta = norm_num(dm.group(1))
        indicators.append({
            "code": code,
            "title": clean(title),
            "values": {y: norm_num(cells[1 + i]) for i, y in enumerate(years)},
            "latest": norm_num(latest),
            "delta_pct": delta,
            "medians": {
                "rf": norm_num(cells[6]),
                "region": norm_num(cells[7]),
                "department": norm_num(cells[8]) if len(cells) > 8 else None,
            },
        })
    return {"years": years, "indicators": indicators}


def parse_charts(text):
    """Отношения «значение / медиана» из createChart_DOP(...)."""
    pattern = re.compile(
        r"createChart_DOP\('([^']+)',\s*(\d+),\s*(\d+),"
        r"\s*(\[[^\]]*\]),\s*(\[[^\]]*\]),\s*(\d+),\s*'([^']*)'\)")
    out = {}
    for m in pattern.finditer(text):
        section = int(m.group(2))
        kind = m.group(7)
        out[(section, kind)] = {
            "flags": json.loads(m.group(4)),
            "ratios": json.loads(m.group(5)),
        }
    return out


def derive_median(value, ratio, flag):
    """Медиана из отношения, если оно не обрезано шкалой.

    Возвращает (median, capped, comparable).
    """
    if not flag or not ratio or value is None:
        return None, False, False
    ratio = float(ratio)
    if ratio >= 1.999:
        return None, True, True
    if ratio <= 0:
        return None, False, False
    return round(value / ratio, 2), False, True


def parse_directions(tables, charts):
    """Раздел III: шесть таблиц направлений деятельности."""
    napde = [t for t in tables if "napde" in (t["attrs"].get("class") or "")]
    if len(napde) < 6:
        raise RuntimeError(f"ожидалось 6 таблиц направлений, найдено {len(napde)}")
    sections = []
    for idx, table in enumerate(napde[:6], start=1):
        title = SECTION_TITLES[idx]
        rows = []
        chart_a = charts.get((idx, "a")) or {}
        chart_b = charts.get((idx, "b")) or {}
        ratios_a = chart_a.get("ratios") or []
        ratios_b = chart_b.get("ratios") or []
        flags_a = chart_a.get("flags") or []
        flags_b = chart_b.get("flags") or []
        data_rows = [r for r in table["rows"] if len(r) >= 4 and re.match(r"\d+\.\d+", r[0]["t"])]
        for i, row in enumerate(data_rows):
            code, title_i, unit, value_raw = (c["t"] for c in row[:4])
            value = norm_num(value_raw)
            ra = ratios_a[i] if i < len(ratios_a) else 0
            rb = ratios_b[i] if i < len(ratios_b) else 0
            fa = flags_a[i] if i < len(flags_a) else 1
            fb = flags_b[i] if i < len(flags_b) else 1
            mp, cap_p, cmp_p = derive_median(value, ra, fa)
            mr, cap_r, cmp_r = derive_median(value, rb, fb)
            rows.append({
                "code": code,
                "title": title_i,
                "unit": unit,
                "value": value,
                "raw": value_raw,
                "median_private": mp,
                "median_private_ratio": ra,
                "median_private_capped": cap_p,
                "median_private_comparable": cmp_p,
                "median_region": mr,
                "median_region_ratio": rb,
                "median_region_capped": cap_r,
                "median_region_comparable": cmp_r,
            })
        sections.append({"code": str(idx), "title": title, "indicators": rows})
    return sections


def _cells(row):
    return [c["t"] for c in row]


def parse_card(tables):
    """Разделы IV и V: контингент, УГН и дополнительные характеристики."""
    by_id = {t["attrs"].get("id"): t for t in tables}

    region = {"organizations": None, "students": None, "city_organizations": None}
    t = by_id.get("info")  # не используется, оставлено для совместимости
    for table in tables:
        attrs = table["attrs"].get("style", "")
        rows = table["rows"]
        if len(rows) == 2 and len(rows[0]) == 2:
            first = _cells(rows[0])
            second = _cells(rows[1])
            if first[0].strip().isdigit() and "организаций высшего образования" in first[1]:
                region["organizations"] = int(first[0].strip())
            if "студентов" in second[1] and second[0].replace(" ", "").isdigit():
                region["students"] = int(second[0].replace(" ", ""))
        if len(rows) == 1 and len(rows[0]) == 2:
            first = _cells(rows[0])
            if first[0].strip().isdigit() and "организаций высшего образования" in first[1]:
                region["city_organizations"] = int(first[0].strip())

    contingent = {}
    for table in tables:
        rows = table["rows"]
        if len(rows) == 2 and len(rows[0]) == 2 and "обучается" in rows[0][0]["t"]:
            c0 = _cells(rows[0])
            c1 = _cells(rows[1])
            m = re.search(r"(\d[\d\s]*)", c0[0])
            contingent["total"] = int(m.group(1).replace(" ", "")) if m else None
            m = re.search(r"(\d[\d\s]*)", c1[0])
            contingent["full_time"] = int(m.group(1).replace(" ", "")) if m else None
            ms = re.search(r"\(([\d,]+)%", c0[1])
            contingent["subject_share"] = norm_num(ms.group(1)) if ms else None
        if len(rows) == 1 and len(rows[0]) == 5 and "бюджетных" in rows[0][0]["t"]:
            cells = _cells(rows[0])
            contingent["budget_share"] = norm_num(cells[1])
            contingent["paid_share"] = norm_num(cells[3])

    branches = []
    t = by_id.get("kont_by_otr")
    if t:
        for row in t["rows"][1:]:
            cells = _cells(row)
            if len(cells) >= 3:
                branches.append({
                    "name": cells[0],
                    "subject_share": norm_num(cells[1]),
                    "contingent": norm_num(cells[2]),
                })

    ugns = []
    t = by_id.get("analis_reg")
    if t:
        for row in t["rows"][1:]:
            cells = _cells(row)
            if len(cells) >= 6:
                code, name = (cells[0].split(" - ", 1) + [""])[:2]
                ugns.append({
                    "code": code.strip(),
                    "name": name.strip(),
                    "contingent": norm_num(cells[1]),
                    "share_org": norm_num(cells[2]),
                    "share_region": norm_num(cells[3]),
                    "vuz_count": int(norm_num(cells[4]) or 0),
                    "branch_count": int(norm_num(cells[5]) or 0),
                })

    # Раздел V: 56 показателей и подстроки «в том числе».
    additional = []
    t = by_id.get("analis_dop")
    if t:
        section = None
        for row in t["rows"]:
            cells = _cells(row)
            if not cells or (len(cells) == 1 and cells[0] in ("", "№ п/п")):
                continue
            if len(cells) == 1:
                section = cells[0]
                continue
            if len(cells) == 4 and cells[0].isdigit():
                # Строка нумерации колонок «1 | 2 | 3 | 4» — не показатель.
                if cells[0] == "1" and cells[1] == "2" and cells[2] == "3":
                    continue
                additional.append({
                    "section": section, "num": int(cells[0]),
                    "title": cells[1], "unit": cells[2],
                    "value": norm_num(cells[3]), "raw": cells[3], "sub": False,
                })
            elif len(cells) == 3:
                additional.append({
                    "section": section, "num": None,
                    "title": cells[0], "unit": cells[1],
                    "value": norm_num(cells[2]), "raw": cells[2], "sub": True,
                })

    return {
        "contingent": contingent,
        "region": region,
        "branches": branches,
        "ugns": ugns,
        "additional": additional,
    }


def write_directions_md(payload, path):
    lines = [
        "# ИУБиП в мониторинге эффективности, выпуск 2025: направления деятельности",
        "",
        f"Источник: {payload['source']}",
        "Значения — как в первоисточнике. Сравнение с медианами: отношение",
        "«значение вуза / медиана»; 1 — ровно медиана, 2 — вдвое выше",
        "(на сайте шкала обрезана), «—» — сравнения нет.",
        "",
    ]
    for section in payload["sections"]:
        lines.append(f"## {section['code']}. {section['title']}")
        lines.append("")
        lines.append("| Код | Показатель | Ед. | Значение | К медиане частных вузов | К медиане региона |")
        lines.append("|---|---|---|---|---|---|")
        for ind in section["indicators"]:
            def cmp_text(ratio, capped, comparable, median):
                if capped:
                    return "≥ 2×"
                if not comparable:
                    return "—"
                if median is not None:
                    return f"×{ratio:.3f} (медиана {median:g})"
                return f"×{ratio:.3f}"
            lines.append(
                f"| {ind['code']} | {ind['title']} | {ind['unit']} | "
                f"{ind['raw']} | "
                f"{cmp_text(ind['median_private_ratio'], ind['median_private_capped'], ind['median_private_comparable'], ind['median_private'])} | "
                f"{cmp_text(ind['median_region_ratio'], ind['median_region_capped'], ind['median_region_comparable'], ind['median_region'])} |")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_card_md(card, info, path):
    lines = [
        "# ИУБиП в мониторинге эффективности, выпуск 2025: карточка организации",
        "",
        "Источник: monitoring.miccedu.ru, карточка вуза id=1971.",
        "",
        "## I. Общие сведения",
        "",
    ]
    for key, value in info.items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## IV. Роль организации в системе подготовки кадров", ""]
    reg = card["region"]
    lines.append(f"- Организаций высшего образования в регионе: {reg.get('organizations')}")
    lines.append(f"- Студентов в регионе: {reg.get('students')}")
    lines.append(f"- Организаций в городе: {reg.get('city_organizations')}")
    cont = card["contingent"]
    lines.append(f"- Обучается студентов: {cont.get('total')}")
    lines.append(f"- В том числе очной формы: {cont.get('full_time')}")
    lines.append(f"- Доля от контингента субъекта РФ: {cont.get('subject_share')}%")
    lines.append(f"- За счёт бюджетных ассигнований: {cont.get('budget_share')}%")
    lines.append(f"- По договорам о платных услугах: {cont.get('paid_share')}%")
    lines += ["", "### Распределение контингента по отраслям наук", ""]
    for b in card["branches"]:
        lines.append(f"- {b['name']}: доля в субъекте {b['subject_share']}%, приведённый контингент {b['contingent']} чел.")
    lines += ["", "### Реализуемые УГН(С)", ""]
    for u in card["ugns"]:
        lines.append(
            f"- {u['code']} — {u['name']}: контингент {u['contingent']} чел., "
            f"доля в организации {u['share_org']}%, доля в регионе {u['share_region']}%, "
            f"вузов в регионе {u['vuz_count']}, филиалов {u['branch_count']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect(html_text=None):
    text = html_text if html_text is not None else fetch_html()
    tables = parse_tables(text)
    info = parse_info(tables)
    e_table = parse_e_table(tables)
    charts = parse_charts(text)
    sections = parse_directions(tables, charts)
    card = parse_card(tables)

    vuz_name = info.get("Наименование образовательной организации", "ИУБиП")
    directions = {
        "source": URL,
        "release": "2025",
        "vuz": {"id": "1971", "name": vuz_name},
        "sections": sections,
    }
    card_payload = {
        "source": URL,
        "release": "2025",
        "vuz": {"id": "1971", "name": vuz_name},
        "info": info,
        "contingent": card["contingent"],
        "region": card["region"],
        "branches": card["branches"],
        "ugns": card["ugns"],
        "additional": card["additional"],
        "e_table": e_table,
    }
    return directions, card_payload


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сбор карточки мониторинга с сайта")
    ap.add_argument("--html", help="сохранённая страница вместо запроса в сеть")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.html:
        html_text = pathlib.Path(args.html).read_text(encoding="utf-8")
    else:
        html_text = fetch_html()

    directions, card = collect(html_text)

    SOURCE.mkdir(parents=True, exist_ok=True)
    (SOURCE / "iubip-directions.json").write_text(
        json.dumps(directions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (SOURCE / "iubip-card.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    write_directions_md(directions, KB / "2025-directions.md")
    write_card_md(card, card["info"], KB / "2025-card.md")

    # Обновляем 2025-ю часть детальной карточки: раздел V уже лежал в
    # iubip-detail.json, но без подстрок «в том числе» и с ручной сверкой.
    detail_path = SOURCE / "iubip-detail.json"
    if detail_path.exists():
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
    else:
        detail = {}
    numbered = [
        {"title": a["title"], "unit": a["unit"], "value": a["raw"]}
        for a in card["additional"] if not a["sub"]
    ]
    detail["2025"] = numbered
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")

    if not args.quiet:
        total = sum(len(s["indicators"]) for s in directions["sections"])
        print(f"направления: {len(directions['sections'])} разделов, {total} показателей")
        print(f"дополнительные характеристики: {len(numbered)} показателей, "
              f"{len(card['additional']) - len(numbered)} подстрок")
        print(f"УГН: {len(card['ugns'])}, отраслей: {len(card['branches'])}")
        print(f"E-таблица: {len(card['e_table']['indicators'])} показателей")
        print(f"записано: {SOURCE / 'iubip-directions.json'}")
        print(f"записано: {SOURCE / 'iubip-card.json'}")
        print(f"записано: {KB / '2025-directions.md'}")
        print(f"записано: {KB / '2025-card.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
