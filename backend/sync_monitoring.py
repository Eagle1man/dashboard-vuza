# -*- coding: utf-8 -*-
"""Синхронизация карточки ИУБиП с monitoring.miccedu.ru.

Зачем отдельный модуль. `collect_monitoring` умеет разобрать страницу,
но не решает, когда её перечитывать, и не обновляет витрины. Здесь —
сторож: проверяет карточку, сравнивает отпечаток данных с прошлым
запуском и пересобирает производные файлы только при изменениях.

Что обновляется:
  * knowledge/official/monitoring/source/iubip-*.json — карточка;
  * knowledge/official/monitoring/2025-*.md — тексты для RAG;
  * knowledge/.../region_indicators.json — строка ИУБиП (или весь
    регион при новом выпуске / обновлении сайта);
  * data/monitoring.json — места, перцентили, медианы;
  * data/analytics.db — витрина чисел для ассистента;
  * индекс knowledge — только если Ollama с моделью эмбеддингов доступна.

Запуск:
    python -m backend.sync_monitoring              проверить и обновить
    python -m backend.sync_monitoring --check      только проверить (1 = есть изменения)
    python -m backend.sync_monitoring --force      пересобрать даже без изменений
    python -m backend.sync_monitoring --full       пересобрать все карточки региона
    python -m backend.sync_monitoring --watch --interval 86400  следить постоянно

Почему полная пересборка региона не на каждом запуске: 38 карточек — это
38 запросов. Полный обход делается при новом выпуске, при смене даты
обновления на сайте и по флагу --full. В обычном режиме обновляется
только строка ИУБиП, а места пересчитываются по уже собранным данным.
"""
import argparse
import hashlib
import html as html_lib
import json
import pathlib
import re
import shutil
import sys
import time
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import analytics, build_monitoring, collect_monitoring, config, embed, ingest  # noqa: E402

KB = ROOT / "knowledge" / "official" / "monitoring"
SOURCE = KB / "source"
REGION_JSON = SOURCE / "region_indicators.json"
STATE_PATH = config.DATA_DIR / "monitoring_sync.json"

VUZ_ID = "1971"
REGION_ID = "10703"

LINK_RE = re.compile(
    r'<a\b[^>]*href=["\'](?:[^"\']*/)?inst\.php\?id=(\d+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
UPDATED_RE = re.compile(r"обновлены\s+(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message):
    print(f"[{now_str()}] {message}", flush=True)


def fetch(url, timeout=30, retries=2):
    """Страница сайта с повторами. Ошибка — исключение, а не пустая строка."""
    last = None
    for attempt in range(retries + 1):
        try:
            return collect_monitoring.fetch_html(url)
        except Exception as exc:  # noqa: BLE001 — сеть бывает любой
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"не удалось получить {url}: {last}")


def site_updated(html_text):
    """Дата обновления материалов на сайте: «обновлены 03.02.2026 г.»."""
    match = UPDATED_RE.search(html_text or "")
    return match.group(1) if match else None


def fingerprint(payload):
    """Стабильный отпечаток данных: порядок ключей не влияет."""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def card_blob(directions, card):
    """Только данные карточки, без служебных путей и дат проверки."""
    return {
        "release": directions.get("release"),
        "sections": directions.get("sections"),
        "info": card.get("info"),
        "contingent": card.get("contingent"),
        "region": card.get("region"),
        "branches": card.get("branches"),
        "ugns": card.get("ugns"),
        "additional": card.get("additional"),
        "e_table": card.get("e_table"),
    }


def _indicator_payload(ind):
    """Показатель в формате region_indicators.json без пустых лет."""
    return {
        "name": ind.get("title") or ind["code"],
        "values": {year: value
                   for year, value in (ind.get("values") or {}).items()
                   if value is not None},
        "medians": {scope: value
                    for scope, value in (ind.get("medians") or {}).items()
                    if value is not None},
    }


def region_entry(directions, card):
    """Строка ИУБиП в формате region_indicators.json."""
    indicators = {}
    for ind in (card.get("e_table") or {}).get("indicators") or []:
        indicators[ind["code"]] = _indicator_payload(ind)
    name = ((card.get("info") or {}).get(
        "Наименование образовательной организации") or "ИУБиП")
    return {"id": VUZ_ID, "name": name, "indicators": indicators}


def region_ids(html_text):
    """Список организаций региона: id и название из ссылок на карточки."""
    out, seen = [], set()
    for match in LINK_RE.finditer(html_text or ""):
        vuz_id = match.group(1)
        if vuz_id in seen:
            continue
        name = html_lib.unescape(TAG_RE.sub(" ", match.group(2)))
        name = " ".join(name.split())
        if not name:
            continue
        seen.add(vuz_id)
        out.append({"id": vuz_id, "name": name})
    return out


def load_json(path, default):
    path = pathlib.Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"не читается {path}: {exc}; считаю отсутствующим")
        return default


def save_json(path, data):
    collect_monitoring._write_text_atomic(
        path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def load_state():
    return load_json(STATE_PATH, {})


def save_state(state):
    save_json(STATE_PATH, state)


def choose_release(state, explicit=None):
    """Год выпуска: явный, прошлый из состояния или свежий на сайте.

    Проверяем следующий год первым: сайт публикует новый выпуск раз в
    год, и сторож должен заметить его без правки кода.
    """
    if explicit:
        html_text = fetch(collect_monitoring.card_url(explicit, VUZ_ID), retries=1)
        return explicit, html_text
    last = int(state.get("release") or 2025)
    errors = []
    for year in (last + 1, last, last - 1):
        try:
            # Проверка выпуска — не загрузка данных: повторы здесь только
            # растягивают запуск, если следующего года ещё нет.
            html_text = fetch(collect_monitoring.card_url(year, VUZ_ID), retries=0)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{year}: {exc}")
            continue
        if ("ИУБиП" in html_text
                and "Наименование образовательной организации" in html_text
                and html_text.count("napde") >= 6):
            return year, html_text
        errors.append(f"{year}: страница без карточки ИУБиП")
    raise RuntimeError("не найден выпуск с карточкой ИУБиП: " + "; ".join(errors))


def scrape_region(year, iubip_entry, old_entries, delay=0.25):
    """Все карточки региона. Упавшую карточку заменяем прежней строкой."""
    region_html = fetch(collect_monitoring.region_url(year, REGION_ID))
    ids = region_ids(region_html)
    if not ids:
        raise RuntimeError("на региональной странице не найдено ни одной карточки")

    old_by_id = {str(e.get("id")): e for e in (old_entries or [])}
    out, stats = [], {"fetched": 0, "kept_old": 0, "failed": 0, "total": len(ids)}
    for item in ids:
        vuz_id = item["id"]
        if vuz_id == VUZ_ID:
            out.append(iubip_entry)
            stats["fetched"] += 1
            continue
        try:
            html_text = fetch(collect_monitoring.card_url(year, vuz_id))
            tables = collect_monitoring.parse_tables(html_text)
            e_table = collect_monitoring.parse_e_table(tables)
            indicators = {
                ind["code"]: _indicator_payload(ind)
                for ind in e_table.get("indicators") or []
            }
            name = item["name"]  # название из регионального списка, не из карточки:
            # у филиала карточка может называть головной вуз, и тогда
            # «лучший результат» подписывался бы не тем именем.
            out.append({"id": vuz_id, "name": name, "indicators": indicators})
            stats["fetched"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            old = old_by_id.get(vuz_id)
            if old:
                out.append(old)
                stats["kept_old"] += 1
                log(f"  карточка {vuz_id} не получена ({exc}); оставлена прежняя")
            else:
                log(f"  карточка {vuz_id} не получена ({exc}); пропущена")
        time.sleep(delay)
    return out, stats


def quick_update(old_entries, entry):
    """Обновить только строку ИУБиП, остальные оставить как были."""
    out = list(old_entries or [])
    for index, item in enumerate(out):
        if str(item.get("id")) == VUZ_ID:
            out[index] = entry
            return out
    out.append(entry)
    return out


def backup_files(paths):
    """Копия данных перед перезаписью; каталог data/backup/ уже в .gitignore."""
    folder = config.BACKUP_DIR / ("monitoring-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in paths:
        path = pathlib.Path(path)
        if path.exists():
            shutil.copy2(path, folder / path.name)
            copied.append(path.name)
    # Не копим бесконечно: 30 последних копий, как у backup.bat.
    folders = sorted(config.BACKUP_DIR.glob("monitoring-*"))
    for old in folders[:-30]:
        shutil.rmtree(old, ignore_errors=True)
    return folder, copied


def rebuild(no_rebuild=False):
    """Пересобрать monitoring.json, analytics.db и индекс knowledge."""
    stats = {"ingest_ok": False}
    if no_rebuild:
        stats["rebuild"] = "пропущено (--no-rebuild)"
        return stats
    build_monitoring.build()
    stats["monitoring"] = "data/monitoring.json"
    analytics.build(verbose=False)
    stats["analytics"] = "data/analytics.db"

    if not embed.ollama_available():
        stats["ingest"] = "пропущено: Ollama недоступна"
        return stats
    warning = ingest.embed_model_warning()
    if warning:
        stats["ingest"] = "пропущено: " + warning
        return stats
    try:
        report = ingest.ingest(prune=False)
        totals = report.get("totals") or {}
        stats["ingest"] = f"документов {totals.get('docs')}, фрагментов {totals.get('chunks')}"
        stats["ingest_ok"] = True
    except Exception as exc:  # noqa: BLE001
        stats["ingest"] = f"ошибка: {exc}"
    return stats


def sync_once(args):
    """Один проход. Возвращает словарь с результатом, не печатает лишнего."""
    state = load_state()
    old_release = str(state.get("release") or "")
    has_state = bool(state)

    if args.html:
        year = args.year or int(state.get("release") or 2025)
        html_text = pathlib.Path(args.html).read_text(encoding="utf-8")
    else:
        year = args.year or None
        year, html_text = choose_release(state, year)

    directions, card = collect_monitoring.collect(html_text, year=year)
    site_date = site_updated(html_text)
    blob = card_blob(directions, card)
    card_fp = fingerprint(blob)

    region_data = load_json(REGION_JSON, {})
    old_entries = region_data.get(str(year)) or []

    changed_release = has_state and str(year) != old_release
    changed_site = bool(has_state and site_date and state.get("site_updated")
                        and site_date != state.get("site_updated"))
    changed_card = (not has_state) or card_fp != state.get("card_fingerprint")
    changed = bool(changed_release or changed_site or changed_card or not old_entries)
    if args.force or args.full:
        changed = True

    result = {
        "release": year,
        "site_updated": site_date,
        "changed": changed,
        "changed_card": changed_card,
        "changed_release": changed_release,
        "changed_site": changed_site,
        "card_fingerprint": card_fp,
        "checked_at": now_str(),
    }

    if args.check:
        # Проверка ничего не пишет: состояние с отпечатками без
        # обновлённых файлов сбило бы следующий запуск.
        if state.get("rebuild_pending") or state.get("ingest_pending"):
            result["rebuild_pending"] = True
            result["ingest_pending"] = bool(state.get("ingest_pending"))
            log("внимание: прошлый запуск оставил витрины или индекс несобранными")
        result["ok"] = True
        return result

    if not changed:
        result["ok"] = True
        # Прошлый запуск с --no-rebuild или без Ollama оставил витрины /
        # индекс несобранными: дособираем сейчас, иначе данные и ответы
        # ассистента разойдутся.
        pending = bool(state.get("rebuild_pending") or state.get("ingest_pending"))
        if pending and not args.no_rebuild:
            stats = rebuild(False)
            result["rebuild"] = stats
            state["rebuild_pending"] = False
            state["ingest_pending"] = not bool(stats.get("ingest_ok"))
            state["last_result"] = "rebuilt" if stats.get("ingest_ok") else "rebuilt-partial"
            log(f"дособрана витрина: {stats}")
        else:
            state["last_result"] = "unchanged"
        state["last_check"] = result["checked_at"]
        save_state(state)
        return result

    # --- изменения: сначала резервная копия, потом запись -----------------
    today = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder, copied = backup_files([
        REGION_JSON,
        SOURCE / "iubip-directions.json",
        SOURCE / "iubip-card.json",
        SOURCE / "iubip-detail.json",
        ROOT / "data" / "monitoring.json",
    ])
    result["backup"] = str(folder)
    result["backup_files"] = copied

    info = collect_monitoring.save(directions, card)
    entry = region_entry(directions, card)

    full = bool(args.full or changed_release or changed_site)
    if full:
        try:
            region_list, region_stats = scrape_region(
                year, entry, old_entries, delay=args.delay)
            result["region"] = region_stats
            result["region_mode"] = "full"
        except Exception as exc:  # noqa: BLE001
            # Регион не собрался — не теряем уже проверенную строку ИУБиП.
            log(f"полный обход региона не удался ({exc}); обновляю только ИУБиП")
            region_list = quick_update(old_entries, entry)
            result["region"] = {"error": str(exc)}
            result["region_mode"] = "quick-fallback"
    else:
        region_list = quick_update(old_entries, entry)
        result["region"] = {"updated": 1, "kept_old": max(0, len(old_entries) - 1)}
        result["region_mode"] = "quick"

    region_data[str(year)] = region_list
    save_json(REGION_JSON, region_data)
    result["region_fingerprint"] = fingerprint(region_list)
    result["saved"] = info

    result["rebuild"] = rebuild(args.no_rebuild)
    rebuild_stats = result["rebuild"]
    if args.no_rebuild:
        rebuild_pending = True
        ingest_pending = True
    else:
        rebuild_pending = False
        ingest_pending = not bool(rebuild_stats.get("ingest_ok"))

    state.update({
        "release": str(year),
        "site_updated": site_date,
        "card_fingerprint": card_fp,
        "region_fingerprint": result["region_fingerprint"],
        "last_check": result["checked_at"],
        "last_change": result["checked_at"],
        "last_result": "updated" if not ingest_pending else "updated-partial",
        "url": collect_monitoring.card_url(year, VUZ_ID),
        "backup": str(folder),
        "rebuild_pending": rebuild_pending,
        "ingest_pending": ingest_pending,
        "counts": {
            "directions": info.get("directions"),
            "detail": info.get("detail"),
            "ugns": info.get("ugns"),
        },
    })
    save_state(state)
    result["ok"] = True
    return result


def print_result(result):
    if not result.get("ok"):
        log("ошибка синхронизации")
        return
    release = result["release"]
    site_date = result.get("site_updated") or "дата не указана"
    if result.get("changed"):
        reasons = []
        if result.get("changed_release"):
            reasons.append("новый выпуск")
        if result.get("changed_site"):
            reasons.append("сайт обновил материалы")
        if result.get("changed_card"):
            reasons.append("изменилась карточка")
        log(f"выпуск {release}, сайт: {site_date}; изменения: "
            + (", ".join(reasons) or "принудительно"))
        saved = result.get("saved") or {}
        if saved:
            log(f"  направления: {saved.get('sections')} разделов / "
                f"{saved.get('directions')} показателей; "
                f"раздел V: {saved.get('detail')}; УГН: {saved.get('ugns')}")
        region = result.get("region") or {}
        if region:
            log(f"  регион ({result.get('region_mode')}): {region}")
        rebuild = result.get("rebuild") or {}
        if rebuild:
            log(f"  пересобрано: {rebuild}")
        if result.get("backup"):
            log(f"  резервная копия: {result['backup']}")
    else:
        log(f"выпуск {release}, сайт: {site_date}; изменений нет")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Синхронизация карточки ИУБиП с monitoring.miccedu.ru")
    ap.add_argument("--check", action="store_true",
                    help="только проверить; код 1 — есть изменения")
    ap.add_argument("--force", action="store_true",
                    help="пересобрать даже без изменений")
    ap.add_argument("--full", action="store_true",
                    help="пересобрать все карточки региона (38 запросов)")
    ap.add_argument("--year", type=int, help="год выпуска (по умолчанию — с сайта)")
    ap.add_argument("--html", help="сохранённая карточка вместо запроса в сеть")
    ap.add_argument("--delay", type=float, default=0.25,
                    help="пауза между карточками региона, сек")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="обновить файлы, но не пересобирать витрины и индекс")
    ap.add_argument("--watch", action="store_true",
                    help="работать постоянно")
    ap.add_argument("--interval", type=int, default=86400,
                    help="интервал проверки в --watch, сек (по умолчанию сутки)")
    args = ap.parse_args(argv)

    if args.watch:
        log(f"сторож запущен: интервал {args.interval} с")
        while True:
            try:
                result = sync_once(args)
                print_result(result)
            except KeyboardInterrupt:
                log("остановлено")
                return 0
            except Exception as exc:  # noqa: BLE001
                log(f"ошибка проверки: {exc}")
            try:
                time.sleep(max(60, args.interval))
            except KeyboardInterrupt:
                log("остановлено")
                return 0

    try:
        result = sync_once(args)
    except Exception as exc:  # noqa: BLE001
        log(f"ошибка синхронизации: {exc}")
        return 2
    print_result(result)
    if args.check:
        return 1 if result.get("changed") else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
