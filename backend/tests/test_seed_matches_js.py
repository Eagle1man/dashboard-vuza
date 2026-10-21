# -*- coding: utf-8 -*-
"""Демо-цифры в базе должны совпадать с тем, что рисует браузер.

Зачем этот тест. Генератор в web/js/data.js детерминированный, и мы
перенесли его в Python, чтобы база была единственным источником
данных. Если две реализации разойдутся хотя бы на копейку в
округлении, на экране и в ответах ИИ будут разные числа — и это
будет выглядеть не как баг, а как «ну, демо-данные». Поэтому
совпадение проверяется сравнением с реальным выводом Node.

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
    # вывод Node в cp1251 и падает на любой кириллице, а не «молча
    # отдаёт мусор».
    out = subprocess.run(
        ["node", str(ROOT / "backend" / "tests" / "dump_js_values.js")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    if out.returncode != 0:
        raise RuntimeError("node не отработал:\n" + (out.stderr or ""))
    return json.loads(out.stdout)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def main():
    js = dump_js()
    all_metrics = build_demo()["metrics"]
    # Сверяется только слой ДЕМО: официальные числа приходят из базы,
    # а не из генератора, и в data.js их нет намеренно.
    py = [m for m in all_metrics if m["source"] == "demo"]
    failures = []
    checked = 0

    official = [m for m in all_metrics if m["source"] == "official"]
    if not official:
        failures.append("в витрине нет ни одной официальной метрики")
    for m in official:
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
        raise SystemExit(f"метрик: js={len(js)}, python={len(py)}")

    for j, p in zip(js, py):
        mid = j["id"]
        checked += 1
        if mid != p["metric_id"]:
            failures.append(f"{mid}: порядок метрик разошёлся ({p['metric_id']})")
            continue
        if j["series"] != p["series"]:
            failures.append(f"{mid}: series\n  js={j['series']}\n  py={p['series']}")
        if not close(j["current"], p["current"]):
            failures.append(f"{mid}: current js={j['current']} py={p['current']}")
        if j["status"] != p["status"]:
            failures.append(f"{mid}: status js={j['status']} py={p['status']}")

        for jd, pd in zip(j["dept"], p["dept"]):
            if jd["deptId"] != pd["dept_id"]:
                failures.append(f"{mid}: порядок подразделений разошёлся")
            elif not close(jd["value"], pd["value"]):
                failures.append(f"{mid}/{pd['dept_id']}: dept value "
                                f"js={jd['value']} py={pd['value']}")

        for jrows, prows in zip(j["drill"], [p["drill"][k] for k in sorted(p["drill"])]):
            if len(jrows) != len(prows):
                failures.append(f"{mid}: число записей drill-down разошлось "
                                f"js={len(jrows)} py={len(prows)}")
                continue
            for jr, pr in zip(jrows, prows):
                if jr["fio"] != pr["fio"]:
                    failures.append(f"{mid}: ФИО js={jr['fio']!r} py={pr['fio']!r}")
                if not close(jr["value"], pr["value"]):
                    failures.append(f"{mid}: drill value js={jr['value']} py={pr['value']}")
                if jr["reason"] != pr["reason"]:
                    failures.append(f"{mid}: reason js={jr['reason']!r} py={pr['reason']!r}")

    if failures:
        print(f"НЕ СОВПАДАЕТ: расхождений {len(failures)} (проверено {checked} метрик)")
        for f in failures[:20]:
            print("  -", f)
        return 1
    print(f"СОВПАДАЕТ: {checked} демо-метрик (ряды, статусы, подразделения "
          f"и все записи drill-down идентичны выводу Node); "
          f"{len(official)} официальных метрик проверены на отсутствие "
          f"выдуманной истории")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())