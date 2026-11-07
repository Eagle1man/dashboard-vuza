# -*- coding: utf-8 -*-
"""Официальные числа в базе должны совпадать с тем, что рисует браузер.

Зачем этот тест. В web/js/data.js лежит резерв для режима без сервера:
если страницу открыли двойным кликом, числа берутся оттуда. Резерв
обязан совпадать с базой, иначе в офлайне ректор увидит одно значение,
а с сервером — другое, и подписать это будет нечем.

Демонстрационных метрик в проекте больше нет: каждое число подписано
документом. Тест заодно проверяет, что выдуманной истории не появилось.

Запуск:  python -m backend.tests.test_seed_matches_js
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.db import build_demo  # noqa: E402


def dump_js():
    # encoding="utf-8" обязателен: без него на Windows Python декодирует
    # вывод Node в cp1251 и падает на любой кириллице.
    out = subprocess.run(
        ["node", str(ROOT / "backend" / "tests" / "dump_js_values.js")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    if out.returncode != 0:
        raise RuntimeError("node не отработал:\n" + (out.stderr or ""))
    return json.loads(out.stdout)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def main():
    js = dump_js()["official"]
    py = build_demo()["metrics"]
    failures = []
    checked = 0

    for m in py:
        if m["source"] != "official":
            failures.append(f"{m['metric_id']}: в витрине осталась "
                            f"демонстрационная метрика ({m['source']})")
        if m["has_history"]:
            failures.append(f"{m['metric_id']}: у официальной метрики "
                            f"не должно быть истории")
        if len(m["series"]) != 1:
            failures.append(f"{m['metric_id']}: официальная метрика должна "
                            f"иметь одну точку, а не {len(m['series'])}")
        if m["drill"] or m["dept"]:
            failures.append(f"{m['metric_id']}: у официальной метрики не должно "
                            f"быть разбивки по кафедрам")

    if len(js) != len(py):
        failures.append(f"официальных метрик: js={len(js)} python={len(py)}")
    else:
        for j, m in zip(js, py):
            if j["id"] != m["metric_id"]:
                failures.append(f"{j['id']}: порядок метрик разошёлся")
                continue
            if j["source"] != "official":
                failures.append(f"{j['id']}: в data.js метрика не официальная")
            if j["current"] is None or not close(j["current"], m["current"]):
                failures.append(f"{j['id']}: значение в data.js "
                                f"js={j['current']} py={m['current']}")
            if j["hasHistory"]:
                failures.append(f"{j['id']}: в data.js у официальной метрики "
                                f"не должно быть истории")
            checked += 1

    if failures:
        print(f"НЕ СОВПАДАЕТ: расхождений {len(failures)} (проверено {checked} метрик)")
        for f in failures[:20]:
            print("  -", f)
        return 1
    print(f"СОВПАДАЕТ: {checked} официальных метрик (значения и отсутствие "
          f"выдуманной истории); демонстрационных метрик нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
