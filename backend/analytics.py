# -*- coding: utf-8 -*-
"""Витрина мониторинга: числа в таблицах, а не только в тексте.

Зачем отдельный файл базы. Вопрос «на сколько E.1 у ИУБиП ниже, чем у
ЮФУ» текстовый поиск решает плохо: нужно найти две строки из 38 и
посчитать разность. Модель, которой дали две строки, считает верно, но
сама арифметика в ответе модели — лишняя точка отказа. Поэтому числа
считает код, а модель формулирует ответ по готовому расчёту.

Почему не Text-to-SQL. Схема здесь известна и мала: два выпуска, шесть
показателей, 38 организаций. Генерация SQL добавила бы класс ошибок
(неверный join, неверный фильтр), не добавив возможностей, и потребовала
бы доступа модели к рабочей базе, где лежат хеши паролей и журнал
действий. Здесь наоборот: отдельный read-only файл, фиксированные
функции, а результат проверяется пересчётом из того же JSON, из которого
собран текст базы знаний.

Источники (единые для витрины и для цитат):
* knowledge/official/monitoring/source/region_indicators.json — ВО, все вузы;
* data/spo.json — СПО, карточка ИУБиП;
* knowledge/official/monitoring/source/iubip-detail.json — своя карточка.

    python -m backend.analytics --build
    python -m backend.analytics --ask "На сколько E.1 у ИУБиП ниже, чем у ЮФУ?"
"""
import argparse
import json
import pathlib
import re
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

ANALYTICS_DB = ROOT / "data" / "analytics.db"
KB = ROOT / "knowledge" / "official" / "monitoring"
REGION_JSON = KB / "source" / "region_indicators.json"
DETAIL_JSON = KB / "source" / "iubip-detail.json"
DIRECTIONS_JSON = KB / "source" / "iubip-directions.json"
SPO_JSON = ROOT / "data" / "spo.json"

IUBIP_ALIASES = ("иубип", "южный университет")
LEVEL_VO, LEVEL_SPO = "ВО", "СПО"

# Единицы по коду: в разных выпусках источник подписывает их по-разному,
# поэтому берём один раз и храним рядом с показателем.
UNITS = {
    "E1": "балл",
    "E2": "тыс. ₽ на НПР",
    "E3": "%",
    "E4": "тыс. ₽ на НПР",
    "E5": "%",
    "E8": "на 100 студентов",
}

# Ключевые слова для ВО. Список ручной намеренно: у официальных названий
# показателей общий префикс («Образовательная деятельность…»), и поиск по
# пересечению слов цепляется за него, как это уже было со строками таблиц.
VO_KEYWORDS = {
    "E1": ("средний балл егэ", "балл егэ", "егэ"),
    "E2": ("ниокр", "научно-исследовательск", "научно исследовательск"),
    "E3": ("иностранн", "международн"),
    "E4": ("доход", "финансово-экономическ", "финансово экономическ"),
    "E5": ("заработн", "зарплат"),
    "E8": ("учён", "учен", "остепенённост", "остепененност", "учёные степени",
           "ученые степени"),
}

# СПО: названия показателей различаются только хвостом («…обучающихся по
# образовательным программам среднего профессионального образования»),
# поэтому пересечение слов даёт ложные совпадения. Здесь явные признаки,
# и если ни один не найден — витрина отказывается, вместо того чтобы
# отдать число соседнего показателя.
SPO_KEYWORDS = {
    "1.1": ("общая численность студентов", "численность студентов",
            "студентов спо", "контингент спо", "сколько студентов спо"),
    "1.2": ("очной форме обучения", "очников"),
    "1.3": ("балл аттестата", "средний балл аттестата"),
    "1.4": ("на основе договоров", "платное обучение"),
    "2.1": ("стран снг", "снг"),
    "2.2": ("кроме стран снг", "иностранных студентов"),
    "3.1": ("получивших диплом", "получивших документ"),
    "3.2": ("государственную итоговую аттестацию", "гиа"),
    "3.3": ("трудоустро", "продолживших обучение"),
    "4.1": ("машин и оборудования",),
    "4.2": ("не старше 5 лет", "оборудования не старше"),
    "5.1": ("повышение квалификации", "прошедших программу"),
    "5.2": ("штатных преподавателей", "мастеров производственного обучения"),
    "7.4": ("медал", "абилимпикс"),
}

# Раздел III карточки мониторинга: направления деятельности. Ключи
# подобраны так, чтобы не перехватывать вопросы про E-показатели:
# «средний балл ЕГЭ» остаётся за E.1, а «минимальный балл ЕГЭ» — за 1.4.
# Это второй, более узкий слой после E-показателей и СПО.
DIRECTION_KEYWORDS = {
    "1.4": ("минимальный балл егэ", "усредненный по реализуемым направлениям"),
    "1.9": ("доля магистратуры", "удельный вес магистратуры"),
    "1.11": ("диплом бакалавра", "диплом специалиста или магистра других"),
    "1.13": ("аспирантов на 100 студентов", "в расчете на 100 студентов"),
    "1.14": ("слушателей из сторонних организаций",),
    "2.1": ("цитирований", "цитируемость публикаций"),
    "2.2": ("публикаций организации", "публикаций в ринц"),
    "2.3": ("белый список", "белом списке"),
    "2.4": ("общий объем ниокр", "объем научно-исследовательских"),
    "2.5": ("удельный вес доходов от ниокр",),
    "2.6": ("собственными силами",),
    "2.7": ("доходы от ниокр",),
    "2.8": ("лицензионных соглашений",),
    "2.9": ("результатов интеллектуальной деятельности",),
    "2.10": ("без ученой степени", "нпр без ученой"),
    "2.11": ("защитивших кандидатские", "защитивших диссертации"),
    "2.12": ("научных журналов",),
    "2.13": ("грантов на выполнение", "грантов на 100"),
    "3.1": ("кроме стран снг", "иностранных студентов кроме"),
    "3.2": ("иностранных студентов из снг", "стран снг"),
    "3.3": ("иностранных студентов, завершивших", "завершивших освоение"),
    "3.4": ("завершивших", "кроме стран снг"),
    "3.5": ("завершивших", "из стран снг"),
    "3.6": ("прошедших обучение за рубежом",),
    "3.7": ("иностранных образовательных организаций",),
    "3.8": ("иностранных граждан из числа нпр",),
    "3.9": ("зарубежных ведущих профессоров",),
    "3.10": ("иностранных аспирантов",),
    "3.11": ("иностранных аспирантов", "из стран снг"),
    "3.12": ("ниокр от иностранных",),
    "3.13": ("образовательной деятельности от иностранных",),
    "4.1": ("приносящей доход деятельности",),
    "4.2": ("доля доходов из средств от приносящей доход",),
    "4.3": ("доходы на численность студентов", "доходы в расчете на студента"),
    "5.1": ("общая площадь учебно-лабораторных",),
    "5.2": ("на праве собственности",),
    "5.3": ("оперативного управления",),
    "5.4": ("безвозмездное пользование",),
    "5.5": ("предоставленных в аренду",),
    "5.6": ("персональных компьютеров",),
    "5.7": ("машин и оборудования не старше 5 лет",),
    "5.8": ("печатных учебных изданий",),
    "6.1": ("кандидата наук",),
    "6.2": ("доктора наук",),
    "6.3": ("кандидата и доктора",),
    "6.4": ("число нпр", "нпр на 100 студентов"),
    "6.5": ("штатных работников ппс",),
}

DIRECTION_CODE_RE = re.compile(r"\b([1-6])\.(\d{1,2})\b")
DIRECTION_HINT = re.compile(r"показател|мониторинг|направлен", re.IGNORECASE)

# Сокращения, которыми люди называют вузы в жизни. В сгенерированных
# вопросах названия официальные, но реальный вопрос ректора выглядит как
# «а у ЮФУ сколько» — без этой таблицы такие вопросы уходили бы в RAG.
VUZ_ALIASES = {
    "иубип": "южный университет",
    "юфу": "южный федеральный университет",
    "дгту": "донской государственный технический университет",
    "ринх": "ростовский государственный экономический университет",
    "ринг": "ростовский государственный экономический университет",
    "консерватори": "ростовская государственная консерватория",
    "педуниверситет": "южный федеральный университет",
    "семинари": "донская духовная семинария",
}

# Слова, которые есть почти в любом официальном названии и потому ничего
# не говорят о том, какой это вуз.
GENERIC_NAME_WORDS = {
    "федеральное", "государственное", "государственный", "государственного",
    "бюджетное", "автономное",
    "образовательное", "учреждение", "высшего", "образования", "частное",
    "организация", "духовная", "филиал", "имени", "университет",
    "институт", "россии", "российской", "федерации",
}

NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
YEAR_RE = re.compile(r"\b(20\d\d)\b")
CODE_RE = re.compile(r"\b[EЕ]\.?\s?([1-8])\b")
SPO_CODE_RE = re.compile(r"\b([1-4])\.(\d)\b")

DIFF_HINT = re.compile(r"на сколько|разниц|отлича|сравн|выше|ниже|больше|меньше",
                       re.IGNORECASE)
RANK_HINT = re.compile(r"какое место|место|рейтинг|перцентил|позици", re.IGNORECASE)
DYNAMICS_HINT = re.compile(r"динамик|измен|рост|снижен|с \d{4} по \d{4}",
                           re.IGNORECASE)
ASKS_COUNT = re.compile(r"\bсколько\b|\bчисленность\b|\bколичество\b", re.IGNORECASE)
ASKS_SHARE = re.compile(r"\bдоля\b|\bпроцент|\bудельный вес\b|%", re.IGNORECASE)


# ---------------------------------------------------------------- нормализация

def norm_name(text: str) -> str:
    """Название к сравнимому виду: кавычки, регистр, пробелы."""
    s = str(text or "").lower()
    for q in "«»„“”\"'`":
        s = s.replace(q, " ")
    s = s.replace("–", "-").replace("—", "-").replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_num(raw: str) -> float | None:
    s = str(raw or "").replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _clean_title(name: str) -> str:
    """Название показателя из источника: склеенное — разделить, длинное — сжать.

    В источнике подпись раздела склеена с расшифровкой без пробела:
    «Образовательная деятельностьСредний балл ЕГЭ…». В контексте модели
    такой строкой нельзя пользоваться: она выглядит как одно слово и
    занимает сотни токенов на каждом вопросе.
    """
    s = re.sub(r"(?<=[а-яё])(?=[А-ЯЁ])", " ", str(name or ""))
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    if len(s) > 140:
        cut = s[:140].rsplit(" ", 1)[0]
        s = cut.rstrip(",;:") + "…"
    return s


def _fmt(value: float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value:.2f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------- схема

SCHEMA = """
CREATE TABLE IF NOT EXISTS indicator (
    level TEXT NOT NULL,
    code  TEXT NOT NULL,
    title TEXT NOT NULL,
    unit  TEXT,
    PRIMARY KEY (level, code)
);
CREATE TABLE IF NOT EXISTS vuz (
    level    TEXT NOT NULL,
    vuz_id   TEXT NOT NULL,
    name     TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    is_iubip INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (level, vuz_id)
);
CREATE TABLE IF NOT EXISTS value (
    level      TEXT NOT NULL,
    release    INTEGER NOT NULL,
    code       TEXT NOT NULL,
    vuz_id     TEXT NOT NULL,
    year       INTEGER NOT NULL,
    value      REAL,
    place      INTEGER,
    total      INTEGER,
    percentile INTEGER,
    PRIMARY KEY (level, release, code, vuz_id, year)
);
CREATE TABLE IF NOT EXISTS median (
    level   TEXT NOT NULL,
    release INTEGER NOT NULL,
    code    TEXT NOT NULL,
    scope   TEXT NOT NULL,
    value   REAL,
    PRIMARY KEY (level, release, code, scope)
);
CREATE TABLE IF NOT EXISTS detail (
    year       INTEGER NOT NULL,
    title      TEXT NOT NULL,
    unit       TEXT,
    value_text TEXT,
    value      REAL,
    PRIMARY KEY (year, title)
);
CREATE TABLE IF NOT EXISTS spo_value (
    year       INTEGER NOT NULL,
    code       TEXT NOT NULL,
    title      TEXT NOT NULL,
    org        REAL,
    region     REAL,
    rf         REAL,
    place      INTEGER,
    total      INTEGER,
    percentile INTEGER,
    median     REAL,
    PRIMARY KEY (year, code)
);
-- Раздел III карточки мониторинга: 57 показателей по направлениям.
-- Медианы на сайте показаны отношением «значение / медиана», поэтому
-- точное число есть только там, где шкала не обрезана (иначе NULL и
-- флаг capped).
CREATE TABLE IF NOT EXISTS direction_indicator (
    release          INTEGER NOT NULL,
    section          TEXT NOT NULL,
    code             TEXT NOT NULL,
    title            TEXT NOT NULL,
    unit             TEXT,
    value            REAL,
    median_private   REAL,
    median_region    REAL,
    ratio_private    REAL,
    ratio_region     REAL,
    capped_private   INTEGER DEFAULT 0,
    capped_region    INTEGER DEFAULT 0,
    PRIMARY KEY (release, code)
);
CREATE INDEX IF NOT EXISTS idx_value_lookup ON value (level, release, code, year);
CREATE INDEX IF NOT EXISTS idx_value_vuz ON value (level, vuz_id, code);
"""


def connect(path: pathlib.Path = None) -> sqlite3.Connection:
    """Только чтение для готовой базы.

    `mode=ro` — не украшение: витрина не должна уметь писать, и это
    свойство базы, а не обещание кода.
    """
    path = pathlib.Path(path or config.ANALYTICS_DB)
    if not path.exists():
        raise FileNotFoundError(
            f"нет витрины {path}. Соберите: python -m backend.analytics --build")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# -------------------------------------------------------------------- сборка

def _rank_rows(conn, level, release, code, year):
    """Места и перцентили внутри области: 100 — лучший результат.

    Считаются один раз при сборке. Если считать при запросе, модель
    увидит место, которое зависит от порядка строк, — а это уже не факт.
    """
    rows = conn.execute(
        "SELECT vuz_id, value FROM value WHERE level=? AND release=? AND code=?"
        " AND year=? AND value IS NOT NULL", (level, release, code, year)).fetchall()
    if not rows:
        return
    ordered = sorted(rows, key=lambda r: (-r["value"], r["vuz_id"]))
    total = len(ordered)
    prev_value, prev_place = None, 0
    for pos, row in enumerate(ordered, start=1):
        place = prev_place if row["value"] == prev_value else pos
        prev_value, prev_place = row["value"], place
        # Перцентиль: 100 — лучший в области. Обратный месту, но
        # учитывает размер области: 5-е из 38 и 5-е из 8 — разные вещи.
        percentile = round(100 * (total - place) / (total - 1)) if total > 1 else 100
        conn.execute(
            "UPDATE value SET place=?, total=?, percentile=? WHERE level=? AND"
            " release=? AND code=? AND vuz_id=? AND year=?",
            (place, total, percentile, level, release, code, row["vuz_id"], year))


def build(path: pathlib.Path = None, verbose: bool = True) -> dict:
    """Пересобирает витрину из JSON. Полностью, а не по частям.

    Полная пересборка здесь честнее инкрементальной: данных мало (тысячи
    строк), а «забыли обновить одну таблицу» — типовая ошибка витрин.
    """
    path = pathlib.Path(path or config.ANALYTICS_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.db")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(str(tmp))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    stats = {"vo_values": 0, "vo_vuz": 0, "spo": 0, "detail": 0}

    # ---- ВО: все вузы области, выпуски 2024/2025 ----------------------
    region = json.loads(REGION_JSON.read_text(encoding="utf-8"))
    # Короткие подписи из карточки ИУБиП: «E.1. Образовательная деятельность»
    # вместо склеенного названия раздела с расшифровкой на 300 символов.
    short = {}
    if (ROOT / "data" / "monitoring.json").exists():
        mon = json.loads((ROOT / "data" / "monitoring.json").read_text(encoding="utf-8"))
        short = {a["code"]: a["label"] for a in mon.get("axes") or []}
    for release, vuzs in region.items():
        for v in vuzs:
            name = str(v.get("name") or "").strip()
            if not name:
                continue
            vuz_id = str(v.get("id") or "")
            is_iubip = 1 if "иубип" in norm_name(name) else 0
            conn.execute(
                "INSERT OR REPLACE INTO vuz (level, vuz_id, name, name_norm, is_iubip)"
                " VALUES (?,?,?,?,?)",
                (LEVEL_VO, vuz_id, name, norm_name(name), is_iubip))
            stats["vo_vuz"] += 1
            for code, ind in (v.get("indicators") or {}).items():
                title = short.get(code) or _clean_title(ind.get("name") or code)
                unit = UNITS.get(code)
                conn.execute(
                    "INSERT OR REPLACE INTO indicator (level, code, title, unit)"
                    " VALUES (?,?,?,?)", (LEVEL_VO, code, title, unit))
                for scope, val in (ind.get("medians") or {}).items():
                    conn.execute(
                        "INSERT OR REPLACE INTO median (level, release, code, scope, value)"
                        " VALUES (?,?,?,?,?)",
                        (LEVEL_VO, int(release), code, scope, float(val)))
                for year, val in (ind.get("values") or {}).items():
                    if val is None:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO value (level, release, code, vuz_id,"
                        " year, value) VALUES (?,?,?,?,?,?)",
                        (LEVEL_VO, int(release), code, vuz_id, int(year), float(val)))
                    stats["vo_values"] += 1

    # Места считаем после загрузки: нужны все строки раздела.
    for release in sorted({int(r) for r in region}):
        codes = [r["code"] for r in conn.execute(
            "SELECT DISTINCT code FROM value WHERE level=? AND release=?",
            (LEVEL_VO, release))]
        years = [r["year"] for r in conn.execute(
            "SELECT DISTINCT year FROM value WHERE level=? AND release=?",
            (LEVEL_VO, release))]
        for code in codes:
            for year in years:
                _rank_rows(conn, LEVEL_VO, release, code, year)

    # ---- СПО: карточка ИУБиП с местом и медианой ----------------------
    spo = json.loads(SPO_JSON.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT OR REPLACE INTO vuz (level, vuz_id, name, name_norm, is_iubip)"
        " VALUES (?,?,?,?,1)",
        (LEVEL_SPO, str(spo.get("vuz", {}).get("id", "1971")),
         str(spo.get("vuz", {}).get("name", "ИУБиП, СПО")), "южный университет спо"))
    for year, block in (spo.get("years") or {}).items():
        for code, ind in (block.get("indicators") or {}).items():
            if code.startswith("old-"):
                continue
            title = _clean_title(ind.get("title") or code)
            unit = "%" if "%" in title else None
            conn.execute(
                "INSERT OR REPLACE INTO indicator (level, code, title, unit)"
                " VALUES (?,?,?,?)",
                (LEVEL_SPO, code, title, unit))
            conn.execute(
                "INSERT OR REPLACE INTO spo_value (year, code, title, org, region, rf,"
                " place, total, percentile, median) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (int(year), code, title,
                 ind.get("org"), ind.get("region"), ind.get("rf"),
                 ind.get("rank"), ind.get("total"), ind.get("percentile"),
                 ind.get("median")))
            stats["spo"] += 1

    # ---- своя карточка: показатели, которых нет в региональных таблицах
    detail = json.loads(DETAIL_JSON.read_text(encoding="utf-8"))
    for year, facts in detail.items():
        for f in facts:
            raw = str(f.get("value") or "")
            conn.execute(
                "INSERT OR REPLACE INTO detail (year, title, unit, value_text, value)"
                " VALUES (?,?,?,?,?)",
                (int(year), str(f.get("title") or ""), f.get("unit"), raw,
                 norm_num(raw)))
            stats["detail"] += 1

    # ---- раздел III: направления деятельности (57 показателей) --------
    stats["directions"] = 0
    if DIRECTIONS_JSON.exists():
        directions = json.loads(DIRECTIONS_JSON.read_text(encoding="utf-8"))
        release = int(directions.get("release") or 2025)
        for section in directions.get("sections") or []:
            for ind in section.get("indicators") or []:
                conn.execute(
                    "INSERT OR REPLACE INTO direction_indicator"
                    "(release, section, code, title, unit, value,"
                    " median_private, median_region, ratio_private, ratio_region,"
                    " capped_private, capped_region)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (release, section.get("title"), ind.get("code"),
                     ind.get("title"), ind.get("unit"), ind.get("value"),
                     ind.get("median_private"), ind.get("median_region"),
                     ind.get("median_private_ratio"),
                     ind.get("median_region_ratio"),
                     1 if ind.get("median_private_capped") else 0,
                     1 if ind.get("median_region_capped") else 0))
                stats["directions"] += 1

    conn.commit()
    conn.close()
    if path.exists():
        path.unlink()
    tmp.replace(path)
    stats["path"] = str(path)
    if verbose:
        print(f"витрина собрана: {path}")
        print(f"  ВО: организаций {stats['vo_vuz']}, значений {stats['vo_values']}")
        print(f"  СПО: показателей {stats['spo']}, своя карточка: {stats['detail']}")
        print(f"  направления деятельности: {stats.get('directions', 0)}")
    return stats


# ------------------------------------------------------------------- разбор

def find_indicator(conn, question: str, level: str = LEVEL_VO):
    """Показатель по коду или по отличительному слову."""
    q = norm_name(question)

    if level == LEVEL_SPO:
        m = SPO_CODE_RE.search(question)
        if m:
            code = f"{m.group(1)}.{m.group(2)}"
            row = conn.execute(
                "SELECT * FROM spo_value WHERE code=? ORDER BY year DESC LIMIT 1",
                (code,)).fetchone()
            if row:
                return {"level": LEVEL_SPO, "code": code, "title": row["title"],
                        "unit": None}
        # Дальше — только явные признаки. Пересечение слов здесь опаснее
        # отказа: «численность студентов» и «численность преподавателей»
        # состоят из одних и тех же слов официальной формулировки.
        q = norm_name(question)
        best, best_len = None, 0
        for code, keys in SPO_KEYWORDS.items():
            for key in keys:
                if key in q and len(key) > best_len:
                    row = conn.execute(
                        "SELECT * FROM spo_value WHERE code=?"
                        " ORDER BY year DESC LIMIT 1", (code,)).fetchone()
                    if row:
                        best = {"level": LEVEL_SPO, "code": code,
                                "title": row["title"], "unit": None}
                        best_len = len(key)
        return best

    m = CODE_RE.search(question)
    if m:
        code = f"E{m.group(1)}"
        row = conn.execute(
            "SELECT * FROM indicator WHERE level=? AND code=?",
            (LEVEL_VO, code)).fetchone()
        if row:
            return {"level": LEVEL_VO, "code": code, "title": row["title"],
                    "unit": row["unit"]}

    best, best_score = None, 0
    for code, keys in VO_KEYWORDS.items():
        for key in keys:
            if key in q:
                score = len(key)
                if score > best_score:
                    row = conn.execute(
                        "SELECT * FROM indicator WHERE level=? AND code=?",
                        (LEVEL_VO, code)).fetchone()
                    if row:
                        best = {"level": LEVEL_VO, "code": code,
                                "title": row["title"], "unit": row["unit"]}
                        best_score = score
    return best


def _contains_seq(hay: list, needle: list) -> bool:
    n = len(needle)
    if not n or n > len(hay):
        return False
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


# Окончания, которые отрезаем при сравнении названий: русские названия
# склоняются, и «федерального государственного бюджетного
# образовательного учреждения» — те же слова, что «федеральное
# государственное бюджетное образовательное учреждение».
_ENDINGS = ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
            "ов", "ев", "ий", "ый", "ой", "ая", "ое", "ые", "ых", "их",
            "ся", "ть", "ет", "ут", "ат", "я", "ы", "и", "а", "о", "е",
            "у", "ю", "ь")


def _stem(token: str) -> str:
    for end in _ENDINGS:
        if len(token) > len(end) + 3 and token.endswith(end):
            return token[:-len(end)]
    return token


GENERIC_STEMS = {_stem(w) for w in GENERIC_NAME_WORDS}


def _distinct_stems(text: str) -> set:
    """Основы слов названия без общих слов и коротких предлогов."""
    return {_stem(t) for t in norm_name(text).split()
            if len(t) > 2} - GENERIC_STEMS


def find_vuz(conn, question: str, level: str = LEVEL_VO,
             exclude_iubip: bool = False):
    """Какой вуз назван в вопросе.

    Порядок и его причины получены на ошибках замера:
    1. полное название как непрерывная последовательность слов в вопросе;
    2. сокращение (ЮФУ, ДГТУ);
    3. основы отличительных слов — реальная речь и склонения.

    Сокращение НЕ может идти первым: в вопросе «ПОЛИТЕХНИЧЕСКИЙ ИНСТИТУТ
    (ФИЛИАЛ) ДГТУ…» слово «ДГТУ» ведёт к головному вузу, и витрина
    отдавала его число вместо числа филиала — правдоподобно и неверно.
    Кавычки не разбираются намеренно: они бывают вложенными («… "ИУБиП"»),
    и разбор по первой кавычке оставляет общий префикс «частное
    образовательное учреждение…», который есть у десятка вузов.
    """
    q = norm_name(question)
    q_tokens = q.split()
    rows = conn.execute("SELECT * FROM vuz WHERE level=?", (level,)).fetchall()
    if exclude_iubip:
        rows = [r for r in rows if not r["is_iubip"]]

    # 1. Полное название внутри вопроса. Именно непрерывной
    # последовательностью: кавычки бывают вложенными («… "ИУБиП"»), и
    # разбор по первой кавычке оставляет общий префикс «частное
    # образовательное учреждение…», который есть у десятка вузов.
    best, best_len = None, 0
    for row in rows:
        name_tokens = row["name_norm"].split()
        if len(name_tokens) <= best_len:
            continue
        if _contains_seq(q_tokens, name_tokens):
            best, best_len = row, len(name_tokens)
    if best:
        return {"vuz_id": best["vuz_id"], "name": best["name"],
                "is_iubip": bool(best["is_iubip"])}

    # 2. Сокращения. Одно сокращение может указывать на головной вуз и на
    # его филиалы: «ЮФУ» есть и у филиала в Новошахтинске. Берём самое
    # короткое название — у головного вуза оно короче, чем у филиала.
    for alias, pattern in VUZ_ALIASES.items():
        if alias not in q:
            continue
        if exclude_iubip and alias in IUBIP_ALIASES:
            continue
        hits = [r for r in rows if pattern in r["name_norm"]]
        if not hits:
            continue
        hits.sort(key=lambda r: (len(r["name_norm"]), r["name_norm"]))
        row = hits[0]
        return {"vuz_id": row["vuz_id"], "name": row["name"],
                "is_iubip": bool(row["is_iubip"])}

    # 3. Основы отличительных слов. Префикс здесь не работает: у двух
    # ростовских филиалов общее начало «Ростовский филиал федерального
    # государственного…», а различают их «правосудия» и «гражданской
    # авиации». Требуем не меньше двух совпадений и покрытия большей части
    # названия: иначе «Московский государственный университет» совпадёт с
    # ростовским филиалом, где слово «московский» тоже есть.
    q_dist = _distinct_stems(question)
    scored = []
    for row in rows:
        name_dist = _distinct_stems(row["name"])
        if len(name_dist) < 2:
            continue
        inter = q_dist & name_dist
        if len(inter) < 2:
            continue
        scored.append((len(inter) / len(name_dist), row))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] >= 0.7:
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.1:
            return None  # два вуза одинаково похожи — не угадываем
        row = scored[0][1]
        return {"vuz_id": row["vuz_id"], "name": row["name"],
                "is_iubip": bool(row["is_iubip"])}
    return None


def _sources(conn, level, release, code):
    """Путь документа и заголовок — чтобы ответ ссылался на реальный файл."""
    if level == LEVEL_SPO:
        return [{"rel_path": "official/monitoring/spo/2025-iubip.md",
                 "doc_title": "ИУБиП в мониторинге СПО, выпуск 2025"}]
    return [{"rel_path": f"official/monitoring/region-{release}.md",
             "doc_title": f"Вузы Ростовской области в мониторинге, выпуск {release}"}]


def _value_row(conn, code, vuz_id, year):
    return conn.execute(
        "SELECT * FROM value WHERE level=? AND code=? AND vuz_id=? AND year=?"
        " ORDER BY release DESC LIMIT 1",
        (LEVEL_VO, code, vuz_id, year)).fetchone()


def _median(conn, release, code, scope="region"):
    row = conn.execute(
        "SELECT value FROM median WHERE level=? AND release=? AND code=? AND scope=?",
        (LEVEL_VO, release, code, scope)).fetchone()
    return row["value"] if row else None


def _display_name(vuz):
    """ИУБиП коротким именем: официальное на 120 символов в каждом
    предложении только раздувает контекст и путает модель."""
    if vuz.get("is_iubip"):
        return "ИУБиП"
    return vuz["name"]


def _where(row):
    """Место и перцентиль словами: модель не должна выводить их сама."""
    if not row or row["place"] is None:
        return ""
    return (f", место {row['place']} из {row['total']}"
            f", перцентиль {row['percentile']}")


def find_direction(conn, question: str):
    """Показатель раздела III по коду или отличительной формулировке."""
    q = norm_name(question)
    if DIRECTION_HINT.search(question):
        m = DIRECTION_CODE_RE.search(q)
        if m:
            code = f"{m.group(1)}.{m.group(2)}"
            row = conn.execute(
                "SELECT * FROM direction_indicator WHERE code=?"
                " ORDER BY release DESC LIMIT 1", (code,)).fetchone()
            if row:
                return row

    best, best_len = None, 0
    for code, keys in DIRECTION_KEYWORDS.items():
        for key in keys:
            if key in q and len(key) > best_len:
                row = conn.execute(
                    "SELECT * FROM direction_indicator WHERE code=?"
                    " ORDER BY release DESC LIMIT 1", (code,)).fetchone()
                if row:
                    best, best_len = row, len(key)
    return best


def _render_direction(row):
    """Текст расчёта по разделу III: значение и сравнение с медианами."""
    def unit(value):
        text = (row["unit"] or "").strip()
        if text.endswith("."):
            text = text[:-1]
        return (value + (" " + text if text else "")).strip()

    lines = [
        "ПОСЧИТАНО ПО ВИТРИНЕ МОНИТОРИНГА (расчёт по данным источника, "
        "не цитата из документа).",
        f"Показатель {row['code']} «{row['title']}». "
        f"ИУБиП: {unit(_fmt(row['value']))}. Выпуск {row['release']}.",
    ]
    for scope, label in (("private", "частных вузов"),
                         ("region", "вузов региона")):
        median = row[f"median_{scope}"]
        ratio = row[f"ratio_{scope}"]
        capped = row[f"capped_{scope}"]
        if capped:
            lines.append(
                f"Медиана {label}: значение вуза не менее чем в 2 раза выше "
                "медианы (на сайте шкала сравнения обрезана, точное число "
                "медианы не публикуется).")
        elif median is not None and ratio is not None:
            lines.append(
                f"Медиана {label}: {unit(_fmt(median))}; "
                f"отношение значения вуза к медиане {ratio:.2f}.")
        else:
            lines.append(f"Сравнение с медианой {label}: нет данных.")
    return "\n".join(lines)


def _answer_direction(conn, row):
    return {
        "kind": "direction",
        "level": LEVEL_VO,
        "release": row["release"],
        "code": row["code"],
        "title": row["title"],
        "unit": row["unit"],
        "numbers": [row["value"], row["median_private"], row["median_region"]],
        "sources": [{
            "rel_path": "official/monitoring/2025-directions.md",
            "doc_title": "ИУБиП в мониторинге, выпуск 2025: направления деятельности",
        }],
        "text": _render_direction(row),
        "formula": "значение и медианы из раздела III карточки мониторинга",
    }


# -------------------------------------------------------------------- запрос

def answer(conn, question: str):
    """Считает ответ по витрине. None — если данных для вопроса нет.

    None здесь означает не «ошибка», а «этот вопрос не числовой или данных
    в витрине нет»: вызывающий продолжает обычным поиском по документам.
    """
    if not question or not question.strip():
        return None

    level = LEVEL_SPO if re.search(r"\bСПО\b|среднего профессионального",
                                   question, re.IGNORECASE) else LEVEL_VO

    # Раздел III отвечает точнее E-показателей там, где формулировка
    # отличительная («минимальный балл ЕГЭ», «белый список», «из стран
    # СНГ»). Явный код E.1…E.8 сильнее: его нельзя перебивать.
    if level == LEVEL_VO and not CODE_RE.search(question):
        direction = find_direction(conn, question)
        if direction:
            return _answer_direction(conn, direction)

    ind = find_indicator(conn, question, level)
    if not ind:
        return None

    # «Сколько» — это количество, а не доля. Показатель в процентах на
    # вопрос «сколько иностранных студентов» отвечает «4,36 %», и это
    # другая метрика, которая выглядит как ответ. Процент отдаём только
    # когда спрашивают про долю.
    if ind.get("unit") == "%" and ASKS_COUNT.search(question) \
            and not ASKS_SHARE.search(question):
        return None

    years = [int(y) for y in YEAR_RE.findall(question)]
    release = max(years) if years else 2025

    if level == LEVEL_SPO:
        row = conn.execute(
            "SELECT * FROM spo_value WHERE code=? AND year=?",
            (ind["code"], release)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM spo_value WHERE code=? ORDER BY year DESC LIMIT 1",
                (ind["code"],)).fetchone()
        if not row:
            return None
        return {
            "kind": "spo",
            "level": LEVEL_SPO,
            "release": row["year"],
            "code": ind["code"],
            "title": row["title"],
            "numbers": [row["org"], row["region"], row["rf"]],
            "sources": _sources(conn, LEVEL_SPO, row["year"], ind["code"]),
            "text": _render_spo(row),
            "formula": f"значения из карточки СПО, выпуск {row['year']}",
        }

    # --- ВО -----------------------------------------------------------
    iubip = find_vuz(conn, "иубип")
    want_diff = bool(DIFF_HINT.search(question))
    want_rank = bool(RANK_HINT.search(question)) and not want_diff
    vuz = find_vuz(conn, question)
    other = find_vuz(conn, question, exclude_iubip=True) if want_diff else None

    # Сравнение двух вузов: в вопросе назван кто-то кроме ИУБиП и есть
    # слово «разница»/«отличается».
    if want_diff and other and iubip:
        other, base = other, iubip
        a = _value_row(conn, ind["code"], other["vuz_id"], release)
        b = _value_row(conn, ind["code"], base["vuz_id"], release)
        if not (a and b) or a["value"] is None or b["value"] is None:
            return None
        diff = b["value"] - a["value"]
        return {
            "kind": "compare_vuz",
            "level": LEVEL_VO, "release": release, "code": ind["code"],
            "title": ind["title"], "unit": ind["unit"],
            "numbers": [b["value"], a["value"], diff],
            "sources": _sources(conn, LEVEL_VO, release, ind["code"]),
            "text": _render_compare_vuz(ind, release, a, other, b, base, diff),
            "formula": f"{_fmt(b['value'])} - {_fmt(a['value'])} = {_fmt(diff)}",
        }

    if vuz is None:
        # Вопрос без названия вуза: если спрашивают «у нас», это ИУБиП,
        # иначе не угадываем — отдаём документному поиску.
        if iubip and re.search(r"\bу нас\b|наш(?:его|ем)?\b", question, re.I):
            vuz = iubip
        else:
            return None

    # Динамика: два года в вопросе, значения берём из одного выпуска.
    if len(set(years)) >= 2 and (want_diff or DYNAMICS_HINT.search(question)):
        y1, y2 = sorted(set(years))[:2]
        r1 = _value_row(conn, ind["code"], vuz["vuz_id"], y1)
        r2 = _value_row(conn, ind["code"], vuz["vuz_id"], y2)
        if not (r1 and r2) or r1["value"] is None or r2["value"] is None:
            return None
        diff = r2["value"] - r1["value"]
        pct = (diff / r1["value"] * 100) if r1["value"] else None
        return {
            "kind": "years",
            "level": LEVEL_VO, "release": max(r1["release"], r2["release"]),
            "code": ind["code"], "title": ind["title"], "unit": ind["unit"],
            "numbers": [r1["value"], r2["value"], diff],
            "sources": _sources(conn, LEVEL_VO, max(r1["release"], r2["release"]),
                                ind["code"]),
            "text": _render_years(ind, vuz, y1, r1["value"], y2, r2["value"],
                                  diff, pct),
            "formula": f"{_fmt(r2['value'])} - {_fmt(r1['value'])} = {_fmt(diff)}"
                       + (f" ({pct:+.2f}%)" if pct is not None else ""),
        }

    # Одно значение: место спрашивают или число — считаем одно и то же,
    # разница только в акценте ответа.
    row = _value_row(conn, ind["code"], vuz["vuz_id"], release)
    if not row or row["value"] is None:
        # Года нет в источнике — отказываем. Раньше здесь брался ближайший
        # год, и вопрос «в 2026 году» получал число 2025-го: правдоподобно
        # и неверно. Пропуск честнее подстановки.
        return None
    med = _median(conn, row["release"], ind["code"])
    return {
        "kind": "rank" if want_rank else "value",
        "level": LEVEL_VO, "release": row["release"], "code": ind["code"],
        "title": ind["title"], "unit": ind["unit"],
        "numbers": [row["value"]] + ([med] if med is not None else []),
        "sources": _sources(conn, LEVEL_VO, row["release"], ind["code"]),
        "text": _render_value(ind, vuz, row, med, want_rank),
        "formula": f"значение из выпуска {row['release']}, год {row['year']}",
    }


# ------------------------------------------------------------------- тексты

def _unit_text(unit):
    return f" {unit}" if unit else ""


def _label(ind):
    """Код и название без дубля: короткая подпись уже начинается с «E.1.»."""
    title = str(ind.get("title") or "")
    code = str(ind.get("code") or "")
    if title.startswith(code) or title.startswith(code.replace("E", "E.")):
        return title
    return f"{code} {title}".strip()


def _release_text(row):
    """«Выпуск 2025» без повтора, когда год совпадает с выпуском."""
    if int(row["year"]) == int(row["release"]):
        return f"Выпуск {row['release']}"
    return f"Выпуск {row['release']}, год {row['year']}"


def _render_value(ind, vuz, row, med, want_rank):
    unit = _unit_text(ind["unit"])
    lines = [
        "ПОСЧИТАНО ПО ВИТРИНЕ МОНИТОРИНГА (расчёт по данным источника, "
        "не цитата из документа).",
        f"Показатель: {_label(ind)}{unit}. {_release_text(row)}.",
        f"{_display_name(vuz)}: {_fmt(row['value'])}{unit}"
        + ("" if want_rank else _where(row)),
    ]
    if med is not None:
        lines.append(f"Медиана по Ростовской области: {_fmt(med)}{unit}.")
    if want_rank and row["place"] is not None:
        lines.append(f"Место в области: {row['place']} из {row['total']}, "
                     f"перцентиль {row['percentile']} (100 — лучший результат).")
    return "\n".join(lines)


def _render_compare_vuz(ind, release, a, other, b, base, diff):
    unit = _unit_text(ind["unit"])
    return "\n".join([
        "ПОСЧИТАНО ПО ВИТРИНЕ МОНИТОРИНГА (расчёт по данным источника, "
        "не цитата из документа).",
        f"Показатель: {_label(ind)}{unit}. Выпуск {release}.",
        f"{_display_name(base)}: {_fmt(b['value'])}{unit}" + _where(b) + ".",
        f"{_display_name(other)}: {_fmt(a['value'])}{unit}" + _where(a) + ".",
        f"Разница (ИУБиП минус вуз сравнения): {_fmt(diff)}{unit}.",
    ])


def _render_years(ind, vuz, y1, v1, y2, v2, diff, pct):
    unit = _unit_text(ind["unit"])
    tail = f" ({pct:+.2f}%)" if pct is not None else ""
    return "\n".join([
        "ПОСЧИТАНО ПО ВИТРИНЕ МОНИТОРИНГА (расчёт по данным источника, "
        "не цитата из документа).",
        f"Показатель: {_label(ind)}{unit}. {_display_name(vuz)}.",
        f"{y1}: {_fmt(v1)}{unit}.",
        f"{y2}: {_fmt(v2)}{unit}.",
        f"Изменение: {_fmt(diff)}{unit}{tail}.",
    ])


def _render_spo(row):
    return "\n".join([
        "ПОСЧИТАНО ПО ВИТРИНЕ МОНИТОРИНГА СПО (расчёт по данным источника, "
        "не цитата из документа).",
        f"Показатель: {row['code']} {row['title']}. Выпуск {row['year']}.",
        f"ИУБиП (СПО): {_fmt(row['org'])}.",
        f"Ростовская область: {_fmt(row['region'])}.",
        f"Российская Федерация: {_fmt(row['rf'])}.",
        ("Место в области: " + str(row["place"]) + " из " + str(row["total"])
         + f", перцентиль {row['percentile']}.") if row["place"] else "",
    ]).strip()


def render(data) -> str:
    """Текст витрины для контекста модели."""
    return data["text"] if data else ""


def as_chunk(data) -> dict:
    """Витрина в виде фрагмента: тогда работает та же нумерация ссылок [1].

    Модель не различает «документ» и «расчёт» по формату — но человек
    видит пометку в тексте и в списке источников: расчёт не выдаётся за
    цитату.
    """
    src = data["sources"][0]
    return {
        "doc_title": src["doc_title"],
        "heading": f"расчёт: {data['code']}",
        "text": data["text"],
        "rel_path": src["rel_path"],
        "is_demo": False,
        # Полный набор полей, как у фрагмента поиска: иначе интерфейс
        # падает на списке источников (doc_type, score, found_by).
        "doc_type": "monitoring",
        "score": None,
        "coverage": None,
        "found_by": "analytics",
    }


def try_answer(question: str, path: pathlib.Path = None):
    """Ответ витрины или None, если витрины нет / вопрос не числовой.

    Витрина — ускоритель, а не обязательный узел: если база не собрана
    (свежая установка, обновление), вопрос должен уйти в обычный поиск, а
    не упасть с ошибкой. Поэтому отсутствие файла здесь не исключение.
    """
    try:
        conn = connect(pathlib.Path(path) if path else None)
    except FileNotFoundError:
        return None
    try:
        return answer(conn, question)
    finally:
        conn.close()


# ---------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="Витрина мониторинга")
    ap.add_argument("--build", action="store_true", help="пересобрать analytics.db")
    ap.add_argument("--ask", help="проверить вопрос на витрине")
    ap.add_argument("--path", help="путь к базе витрины")
    args = ap.parse_args(argv)

    if args.build:
        build(pathlib.Path(args.path) if args.path else None)
    if args.ask:
        conn = connect(pathlib.Path(args.path) if args.path else None)
        data = answer(conn, args.ask)
        conn.close()
        if not data:
            print("витрина не отвечает на этот вопрос — пойдёт поиск по документам")
            return 1
        print(data["text"])
        print("\nформула:", data["formula"])
        print("источник:", data["sources"][0]["rel_path"])
    if not args.build and not args.ask:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
