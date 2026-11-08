# -*- coding: utf-8 -*-
"""Сборка data/monitoring.json из региональной выгрузки мониторинга.

Зачем отдельный шаг. `region_indicators.json` — это сырые карточки всех
вузов области: пять лет значений и медианы по РФ, субъекту и
ведомственной принадлежности. Дашборду нужен компактный файл с местами,
перцентилями и динамикой, посчитанными один раз при сборке — а не в
браузере и не в ответе модели.

Что считается:
  * место и перцентиль среди вузов области за последний год выпуска;
  * медиана области и лучший результат (вуз + значение);
  * динамика последнего года к предыдущему, %;
  * полный пятилетний ряд — как в таблице раздела II на сайте.

    python -m backend.build_monitoring
"""
import argparse
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

REGION_JSON = (ROOT / "knowledge" / "official" / "monitoring" / "source"
               / "region_indicators.json")
OUT_JSON = ROOT / "data" / "monitoring.json"

# Радар: только показатели, которые публикуются в выпуске 2025.
RADAR_CODES = ["E1", "E2", "E3", "E4", "E8"]

# Полная таблица раздела II, как на сайте. E.5 в выпуске 2025 не
# публикуется (закрытые данные Росстата), но в выпуске 2024 он есть.
E_AXES = [
    {"code": "E1", "label": "E.1. Образовательная деятельность", "unit": "балл"},
    {"code": "E2", "label": "E.2. Научно-исследовательская деятельность",
     "unit": "тыс. ₽ на НПР"},
    {"code": "E3", "label": "E.3. Международная деятельность", "unit": "%"},
    {"code": "E4", "label": "E.4. Финансово-экономическая деятельность",
     "unit": "тыс. ₽ на НПР"},
    {"code": "E5", "label": "E.5. Заработная плата ППС", "unit": "%"},
    {"code": "E8", "label": "E.8. Число НПР с учёными степенями",
     "unit": "на 100 студентов"},
]


def norm_name(text):
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def is_iubip(name):
    n = norm_name(name)
    return "иубип" in n or "южный университет" in n


def finite(value):
    return isinstance(value, (int, float)) and value is not None


def pct_change(new, old):
    if not finite(new) or not finite(old) or old == 0:
        return None
    return round((new - old) / abs(old) * 100, 1)


def build_release(vuzs, release):
    """Один выпуск: ряд ИУБиП, места, медианы и лучший результат."""
    iubip = next((v for v in vuzs if str(v.get("id")) == "1971"), None)
    if iubip is None:
        iubip = next((v for v in vuzs if is_iubip(v.get("name"))), None)
    if iubip is None:
        raise RuntimeError(f"в выпуске {release} нет карточки ИУБиП")

    all_years = set()
    for v in vuzs:
        for ind in (v.get("indicators") or {}).values():
            all_years.update((ind.get("values") or {}).keys())
    years = sorted(all_years)

    indicators = {}
    for code, ind in (iubip.get("indicators") or {}).items():
        values = {y: val for y, val in (ind.get("values") or {}).items()
                  if finite(val)}
        if not values:
            continue
        latest_year = max(values)
        prev_year = years[years.index(latest_year) - 1] if latest_year in years and years.index(latest_year) > 0 else None
        latest = values[latest_year]
        prev = values.get(prev_year) if prev_year else None

        candidates = []
        for v in vuzs:
            other = (v.get("indicators") or {}).get(code) or {}
            val = (other.get("values") or {}).get(latest_year)
            if finite(val):
                candidates.append((float(val), v.get("name") or ""))
        candidates.sort(key=lambda x: -x[0])
        values_sorted = [c[0] for c in candidates]
        total = len(values_sorted)
        place = 1 + sum(1 for val in values_sorted if val > latest)
        percentile = round(100 * (total - place) / (total - 1)) if total > 1 else 100
        best = ({"value": candidates[0][0], "name": candidates[0][1]}
                if candidates else None)

        indicators[code] = {
            "label": next(a["label"] for a in E_AXES if a["code"] == code)
                     if code in {a["code"] for a in E_AXES} else code,
            "unit": next(a["unit"] for a in E_AXES if a["code"] == code)
                    if code in {a["code"] for a in E_AXES} else "",
            "value": latest,
            "values": values,
            "year": int(latest_year),
            "delta_pct": pct_change(latest, prev),
            "rank": place,
            "total": total,
            "percentile": percentile,
            "median": round(statistics.median(values_sorted), 2) if values_sorted else None,
            "medians": {k: v for k, v in (ind.get("medians") or {}).items()
                        if finite(v)},
            "best": best,
        }

    radar = [indicators[c]["percentile"] for c in RADAR_CODES if c in indicators]
    return {
        "total": len(vuzs),
        "years": years,
        "indicators": indicators,
        "average_percentile": round(sum(radar) / len(radar)) if radar else None,
    }


def build(path=None):
    raw = json.loads(REGION_JSON.read_text(encoding="utf-8"))
    releases = {str(release): build_release(vuzs, str(release))
                for release, vuzs in raw.items()}

    iubip = None
    for vuzs in raw.values():
        for v in vuzs:
            if str(v.get("id")) == "1971" or is_iubip(v.get("name")):
                iubip = v
                break
        if iubip:
            break

    data = {
        "source": "Мониторинг эффективности деятельности вузов, monitoring.miccedu.ru",
        "vuz": {"id": "1971", "name": iubip.get("name") if iubip else "ИУБиП"},
        "axes": [a for a in E_AXES if a["code"] in RADAR_CODES],
        "e_axes": E_AXES,
        "years": releases,
        "notes": {
            "2025": "E.5 не публикуется: данные Росстата о заработной плате "
                    "за 2024 год носят закрытый характер.",
        },
    }

    path = pathlib.Path(path or OUT_JSON)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return data, path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сборка monitoring.json")
    ap.add_argument("--out", help="куда писать (по умолчанию data/monitoring.json)")
    args = ap.parse_args(argv)
    data, path = build(args.out)
    print(f"собрано: {path}")
    for release, block in data["years"].items():
        print(f"  выпуск {release}: организаций {block['total']}, "
              f"показателей {len(block['indicators'])}, "
              f"средний перцентиль {block['average_percentile']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
