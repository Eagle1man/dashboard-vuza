# -*- coding: utf-8 -*-
"""Синхронизация карточки СПО ИУБиП с monitoring.miccedu.ru.

Устроено так же, как сторож ВО (sync_monitoring.py):
  * отпечаток карточки и выпуск — что проверять;
  * быстрый режим обновляет только строку ИУБиП, места считаются по
    сохранённым карточкам организаций региона (source/spo_region.json);
  * --full перечитывает все 142 карточки СПО региона;
  * обновляются data/spo.json, текст для RAG и analytics.db.

Запуск:
    python -m backend.sync_spo                проверить и обновить
    python -m backend.sync_spo --check        только проверить (1 = есть изменения)
    python -m backend.sync_spo --full         пересобрать все карточки региона
    python -m backend.sync_spo --watch --interval 604800   раз в неделю

Через общий сторож: python -m backend.sync_monitoring --spo ...
"""
import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import spo  # noqa: E402
from backend import sync_monitoring as sm  # noqa: E402

SPO_JSON = ROOT / "data" / "spo.json"
SPO_REGION = ROOT / "knowledge" / "official" / "monitoring" / "source" / "spo_region.json"
SPO_MD_DIR = ROOT / "knowledge" / "official" / "monitoring" / "spo"


def load_spo():
    data = sm.load_json(SPO_JSON, {})
    data.setdefault("years", {})
    return data


def load_region():
    return sm.load_json(SPO_REGION, {})


def choose_release(state, explicit=None):
    """Год выпуска СПО: явный, прошлый из состояния или свежий на сайте."""
    if explicit:
        html_text = sm.fetch(spo.card_url(explicit), retries=1)
        return explicit, html_text
    spo_state = state.get("spo") or {}
    last = int(spo_state.get("release") or state.get("release") or 2025)
    errors = []
    for year in (last + 1, last, last - 1):
        try:
            html_text = sm.fetch(spo.card_url(year), retries=0)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{year}: {exc}")
            continue
        if ("наименование образовательной организации" in html_text.lower()
                and html_text.count("napde") >= 7):
            return year, html_text
        errors.append(f"{year}: страница без карточки СПО")
    raise RuntimeError("не найден выпуск СПО: " + "; ".join(errors))


def scrape_region(year, iubip_entry, old_entries, delay=0.2):
    """Все карточки СПО региона. Упавшую заменяем прежней строкой."""
    region_html = sm.fetch(spo.region_url(year))
    ids = spo.region_ids(region_html)
    if not ids:
        raise RuntimeError("на региональной странице СПО не найдено карточек")

    old_by_id = {str(entry.get("id")): entry for entry in (old_entries or [])}
    out = []
    stats = {"fetched": 0, "kept_old": 0, "failed": 0, "total": len(ids)}
    for item in ids:
        vuz_id = item["id"]
        if vuz_id == spo.VUZ_ID:
            out.append(iubip_entry)
            stats["fetched"] += 1
            continue
        try:
            html_text = sm.fetch(spo.card_url(year, vuz_id))
            card = spo.parse_card(html_text)
            out.append({
                "id": vuz_id,
                "name": item["name"],
                "indicators": {
                    code: ind["org"]
                    for code, ind in (card.get("indicators") or {}).items()
                },
            })
            stats["fetched"] += 1
            if stats["fetched"] % 20 == 0:
                sm.log(f"  СПО: получено {stats['fetched']} из {len(ids)}")
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            old = old_by_id.get(vuz_id)
            if old:
                out.append(old)
                stats["kept_old"] += 1
                sm.log(f"  СПО {vuz_id} не получена ({exc}); оставлена прежняя")
            else:
                sm.log(f"  СПО {vuz_id} не получена ({exc}); пропущена")
        time.sleep(delay)
    return out, stats


def quick_update(old_entries, entry):
    out = list(old_entries or [])
    for index, item in enumerate(out):
        if str(item.get("id")) == spo.VUZ_ID:
            out[index] = entry
            return out
    out.append(entry)
    return out


def sync_once(args):
    state = sm.load_state()
    spo_state = state.get("spo") or {}

    if args.html:
        year = args.year or int(spo_state.get("release")
                                or state.get("release") or 2025)
        html_text = pathlib.Path(args.html).read_text(encoding="utf-8")
    else:
        year, html_text = choose_release(state, args.year)

    card = spo.parse_card(html_text)
    card_fp = sm.fingerprint(card)
    release = str(year)

    region_cache = load_region()
    old_orgs = (region_cache.get("organizations") or []
                if str(region_cache.get("release")) == release else [])

    changed_release = bool(spo_state) and release != str(spo_state.get("release"))
    changed_card = (not spo_state) or card_fp != spo_state.get("card_fingerprint")
    changed = bool(changed_release or changed_card or not old_orgs)
    if args.force or args.full:
        changed = True

    result = {
        "release": year,
        "changed": changed,
        "changed_card": changed_card,
        "changed_release": changed_release,
        "card_fingerprint": card_fp,
        "checked_at": sm.now_str(),
    }

    if args.check:
        result["ok"] = True
        return result

    if not changed:
        state.setdefault("spo", {})
        state["spo"].update({
            "release": release,
            "last_check": result["checked_at"],
            "last_result": "unchanged",
        })
        sm.save_state(state)
        result["ok"] = True
        return result

    md_path = SPO_MD_DIR / f"{release}-iubip.md"
    folder, copied = sm.backup_files([SPO_JSON, SPO_REGION, md_path])
    result["backup"] = str(folder)
    result["backup_files"] = copied

    entry = spo.card_entry(card)
    full = bool(args.full or changed_release or not old_orgs)
    if full:
        try:
            organizations, region_stats = scrape_region(
                year, entry, old_orgs, delay=args.delay)
            result["region"] = region_stats
            result["region_mode"] = "full"
        except Exception as exc:  # noqa: BLE001
            if not old_orgs:
                raise RuntimeError(
                    f"полный обход СПО не удался, а прежних данных за {release} нет: "
                    f"{exc}") from exc
            sm.log(f"полный обход СПО не удался ({exc}); обновляю только ИУБиП")
            organizations = quick_update(old_orgs, entry)
            result["region"] = {"error": str(exc)}
            result["region_mode"] = "quick-fallback"
    else:
        organizations = quick_update(old_orgs, entry)
        result["region"] = {"updated": 1,
                            "kept_old": max(0, len(old_orgs) - 1)}
        result["region_mode"] = "quick"

    region_payload = {
        "source": spo.region_url(year),
        "release": release,
        "total": len(organizations),
        "organizations": organizations,
    }
    sm.save_json(SPO_REGION, region_payload)
    result["region_fingerprint"] = sm.fingerprint(organizations)

    spo_data = load_spo()
    previous_release = max(
        [key for key in spo_data.get("years", {}) if key < release], default=None)
    previous = ((spo_data["years"][previous_release].get("indicators")
                 if previous_release else None) or {})
    release_indicators = spo.build_release(card, organizations, previous)
    spo_data["source"] = "Мониторинг СПО, monitoring.miccedu.ru"
    spo_data["vuz"] = {"id": spo.VUZ_ID,
                       "name": "Южный университет (ИУБиП), СПО"}
    spo_data["years"][release] = {
        "total": len(organizations),
        "indicators": release_indicators,
    }
    sm.save_json(SPO_JSON, spo_data)
    spo.write_spo_md(spo_data, release, md_path)
    result["saved"] = {"release": release,
                       "indicators": len(release_indicators),
                       "organizations": len(organizations)}
    result["rebuild"] = sm.rebuild(args.no_rebuild)

    state["spo"] = {
        "release": release,
        "card_fingerprint": card_fp,
        "region_fingerprint": result["region_fingerprint"],
        "last_check": result["checked_at"],
        "last_change": result["checked_at"],
        "last_result": "updated",
        "url": spo.card_url(year),
        "backup": str(folder),
        "counts": {"indicators": len(release_indicators),
                   "organizations": len(organizations)},
    }
    sm.save_state(state)
    result["ok"] = True
    return result


def print_result(result):
    if not result.get("ok"):
        sm.log("ошибка синхронизации СПО")
        return
    if result.get("changed"):
        reasons = []
        if result.get("changed_release"):
            reasons.append("новый выпуск")
        if result.get("changed_card"):
            reasons.append("изменилась карточка")
        sm.log(f"СПО, выпуск {result['release']}; изменения: "
               + (", ".join(reasons) or "принудительно"))
        if result.get("saved"):
            sm.log(f"  показателей: {result['saved']['indicators']}, "
                   f"организаций: {result['saved']['organizations']}")
        if result.get("region"):
            sm.log(f"  регион ({result.get('region_mode')}): {result['region']}")
        if result.get("rebuild"):
            sm.log(f"  пересобрано: {result['rebuild']}")
        if result.get("backup"):
            sm.log(f"  резервная копия: {result['backup']}")
    else:
        sm.log(f"СПО, выпуск {result['release']}; изменений нет")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Синхронизация карточки СПО ИУБиП")
    ap.add_argument("--check", action="store_true",
                    help="только проверить; код 1 — есть изменения")
    ap.add_argument("--force", action="store_true",
                    help="пересобрать даже без изменений")
    ap.add_argument("--full", action="store_true",
                    help="пересобрать все карточки СПО региона (142 запроса)")
    ap.add_argument("--year", type=int, help="год выпуска")
    ap.add_argument("--html", help="сохранённая карточка вместо запроса в сеть")
    ap.add_argument("--delay", type=float, default=0.2,
                    help="пауза между карточками региона, сек")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="обновить файлы, но не пересобирать витрины и индекс")
    ap.add_argument("--watch", action="store_true", help="работать постоянно")
    ap.add_argument("--interval", type=int, default=604800,
                    help="интервал проверки в --watch, сек (по умолчанию неделя)")
    args = ap.parse_args(argv)

    if args.watch:
        sm.log(f"сторож СПО запущен: интервал {args.interval} с")
        while True:
            try:
                print_result(sync_once(args))
            except KeyboardInterrupt:
                sm.log("остановлено")
                return 0
            except Exception as exc:  # noqa: BLE001
                sm.log(f"ошибка проверки СПО: {exc}")
            try:
                time.sleep(max(300, args.interval))
            except KeyboardInterrupt:
                sm.log("остановлено")
                return 0

    try:
        result = sync_once(args)
    except Exception as exc:  # noqa: BLE001
        sm.log(f"ошибка синхронизации СПО: {exc}")
        return 2
    print_result(result)
    if args.check:
        return 1 if result.get("changed") else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
