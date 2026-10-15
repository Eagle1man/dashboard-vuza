# -*- coding: utf-8 -*-
"""
Схема хранилища и перенос демонстрационных метрик в базу.

Одно хранилище, четыре слоя:
  1. справочники (dim_*)      — что вообще измеряем
  2. витрина (fact_*)         — цифры по периодам и подразделениям
  3. корпус (doc/doc_chunk/   — тексты для RAG: куски, полнотекст, векторы
     chunk_fts/chunk_vec)
  4. журнал (ask_log)         — история ответов

Почему один файл SQLite, а не несколько баз и не PostgreSQL:
одна база = один бэкап (копия файла), ноль служб, ноль паролей.
FTS5 входит в SQLite, поэтому полнотекстовый поиск не требует
ничего доустанавливать.
"""
import json
import math
import sqlite3
import time
from pathlib import Path

from . import config

U32 = 0xFFFFFFFF

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- 1. Справочники --------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_metric (
  metric_id TEXT PRIMARY KEY,
  block_id  TEXT NOT NULL,
  title     TEXT NOT NULL,
  unit      TEXT,
  dir       TEXT,          -- 'higher' | 'lower'
  target    REAL,
  ord       INTEGER
);

-- Синонимы: по ним и вопрос ректора, и FTS-поиск понимают,
-- что «отсев» и «отчисления» — одна и та же метрика.
CREATE TABLE IF NOT EXISTS metric_alias (
  metric_id TEXT NOT NULL,
  alias     TEXT NOT NULL,
  PRIMARY KEY (metric_id, alias)
);

CREATE TABLE IF NOT EXISTS dim_org (
  org_id TEXT PRIMARY KEY,
  title  TEXT NOT NULL,
  ord    INTEGER
);

CREATE TABLE IF NOT EXISTS dim_period (
  period_id TEXT PRIMARY KEY,
  ord       INTEGER NOT NULL,
  label     TEXT NOT NULL,
  year      INTEGER NOT NULL,
  month     INTEGER NOT NULL
);

-- Образовательные программы по ФГОС. Это справочник, а не витрина:
-- у программы нет временного ряда, есть код, уровень и годы
-- утверждения и последнего изменения стандарта. Год изменения —
-- живой сигнал: по нему видно, где стандарт устарел.
CREATE TABLE IF NOT EXISTS dim_standard (
  code      TEXT PRIMARY KEY,
  level     TEXT NOT NULL,
  title     TEXT NOT NULL,
  approved  INTEGER,
  updated   INTEGER
);

-- 2. Витрина ------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_metric (
  period_id TEXT NOT NULL,
  metric_id TEXT NOT NULL,
  org_id    TEXT NOT NULL,
  value     REAL NOT NULL,
  status    TEXT,           -- заполняется только для последнего периода
  PRIMARY KEY (period_id, metric_id, org_id)
);
CREATE INDEX IF NOT EXISTS fact_metric_metric ON fact_metric(metric_id, period_id);

CREATE TABLE IF NOT EXISTS fact_detail (
  record_id    TEXT PRIMARY KEY,
  period_id    TEXT NOT NULL,
  metric_id    TEXT NOT NULL,
  org_id       TEXT NOT NULL,
  subject_hash TEXT,         -- для реальных данных: хэш, а не ФИО
  subject_label TEXT,        -- для демо: ФИО (152-ФЗ см. README)
  value        REAL,
  reason       TEXT
);
CREATE INDEX IF NOT EXISTS fact_detail_metric ON fact_detail(metric_id);

-- 3. Корпус документов --------------------------------------------
CREATE TABLE IF NOT EXISTS doc (
  doc_id   INTEGER PRIMARY KEY,
  rel_path TEXT NOT NULL UNIQUE,
  title    TEXT,
  doc_type TEXT,
  sha256   TEXT,
  n_pages  INTEGER,
  n_chunks INTEGER DEFAULT 0,
  is_demo  INTEGER DEFAULT 0,
  added_at TEXT,
  block_id TEXT      -- блок дашборда, к которому относится документ
);
CREATE INDEX IF NOT EXISTS doc_sha ON doc(sha256);

-- Документ может относиться к нескольким блокам сразу: протокол
-- учёного совета обсуждает и успеваемость, и трудоустройство, и науку.
-- Один блок на документ заставил бы либо терять документ, либо
-- показывать его всем подряд.
CREATE TABLE IF NOT EXISTS doc_block (
  doc_id   INTEGER NOT NULL REFERENCES doc(doc_id) ON DELETE CASCADE,
  block_id TEXT NOT NULL,
  PRIMARY KEY (doc_id, block_id)
);
CREATE INDEX IF NOT EXISTS doc_block_block ON doc_block(block_id);

-- Что лежит на каждой странице документа.
-- Нужно, чтобы система могла честно сказать «на этой странице график,
-- текст не извлечён», вместо того чтобы молчать и выдавать уверенный
-- ответ по неполным данным.
CREATE TABLE IF NOT EXISTS doc_page (
  doc_id    INTEGER NOT NULL REFERENCES doc(doc_id) ON DELETE CASCADE,
  page_no   INTEGER NOT NULL,
  kind      TEXT NOT NULL,        -- text | table | image
  n_chars   INTEGER DEFAULT 0,
  n_images  INTEGER DEFAULT 0,
  n_tables  INTEGER DEFAULT 0,
  PRIMARY KEY (doc_id, page_no)
);
CREATE INDEX IF NOT EXISTS doc_page_kind ON doc_page(kind);

CREATE TABLE IF NOT EXISTS doc_chunk (
  chunk_id   INTEGER PRIMARY KEY,
  doc_id     INTEGER NOT NULL REFERENCES doc(doc_id) ON DELETE CASCADE,
  ord        INTEGER NOT NULL,
  heading    TEXT,
  text       TEXT NOT NULL,
  n_chars    INTEGER,
  -- Уникальность хэша — в пределах ДОКУМЕНТА, а не базы. Одинаковая
  -- формулировка в двух разных документах (типовой пункт, ссылка на
  -- общий регламент) — законный случай: оба документа должны
  -- индексироваться и ссылаться каждый на свой источник.
  -- Внутри документа одинаковые куски не хранятся дважды.
  chunk_hash TEXT NOT NULL,
  UNIQUE (doc_id, chunk_hash)
);
CREATE INDEX IF NOT EXISTS doc_chunk_doc ON doc_chunk(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  text, heading, chunk_id UNINDEXED,
  tokenize = 'unicode61 remove_diacritics 2'
);

-- Векторы хранятся как BLOB float32. При корпусе вуза (5-9 тыс. чанков)
-- полный перебор косинуса через numpy занимает единицы миллисекунд,
-- поэтому отдельный векторный сервер не нужен.
CREATE TABLE IF NOT EXISTS chunk_vec (
  chunk_id INTEGER PRIMARY KEY,
  doc_id   INTEGER NOT NULL,
  dim      INTEGER NOT NULL,
  vec      BLOB NOT NULL,
  backend  TEXT NOT NULL      -- 'ollama:nomic-embed-text' | 'hash-fallback'
);
CREATE INDEX IF NOT EXISTS chunk_vec_doc ON chunk_vec(doc_id);

-- 4. Журнал --------------------------------------------------------
CREATE TABLE IF NOT EXISTS ask_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT,
  question   TEXT,
  answer     TEXT,
  chunk_ids  TEXT,
  model      TEXT,
  hits       INTEGER,
  latency_ms INTEGER
);
"""


# --------------------------------------------------------------------
# соединение
# --------------------------------------------------------------------
def connect(path=None, create=True):
    path = Path(path or config.DB_PATH)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn=None):
    own = conn is None
    conn = conn or connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    if own:
        conn.close()
    return True


def _migrate(conn):
    """Мелкие правки схемы для уже созданных баз.

    Отдельная функция нужна потому, что CREATE TABLE IF NOT EXISTS не
    добавляет колонку в существующую таблицу: на старой базе установки
    новый столбец просто не появился бы, и документы молча получили бы
    block_id = NULL, а специалисты агентов потеряли бы фильтр.
    """
    for table, column, decl in (("doc", "block_id", "TEXT"),):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


# --------------------------------------------------------------------
# Демонстрационные данные.
#
# Числа должны совпадать с тем, что рисует web/js/data.js.
# Генератор там детерминированный (mulberry32), значит и здесь можно
# получить ровно те же значения — и это проверяется тестом
# backend/tests/test_seed_matches_js.py, который сверяет вывод
# Python с выводом Node.
# --------------------------------------------------------------------

# Поля блоков повторяют web/js/data.js: фронтенд фильтрует блоки по
# роли через blocksForRole, поэтому role обязан быть в ответе БД,
# а не остаться только в браузере.
# Блоки соответствуют зонам ответственности проректоров и
# направлениям организационной структуры университета. Финансового
# и кадрового блоков в структуре нет — они не появились и здесь.
BLOCKS = [
    ("academic", "Академическая политика", "🎓",
     ["rector", "prorector", "admin"],
     "Приём, контингент, действующие образовательные программы."),
    ("results", "Учебные результаты", "📚",
     ["rector", "prorector", "admin"],
     "Успеваемость, качество знаний, промежуточная аттестация."),
    ("vnoko", "Качество образования (ВНОКО)", "✅",
     ["rector", "prorector", "admin"],
     "Внутренняя независимая оценка качества: опросы и план-факт."),
    ("science", "Наука и метакомпетенции", "🔬",
     ["rector", "prorector", "admin"],
     "Гранты, публикации, интеллектуальная собственность, НИЛ."),
    ("upr", "Воспитательная работа и молодёжная политика", "🤝",
     ["rector", "prorector", "admin"],
     "Студенческий совет, юридическая клиника, центр карьеры."),
    ("digital", "Цифровые сервисы и ЭИОС", "💻",
     ["rector", "prorector", "admin"],
     "Электронная информационно-образовательная среда, LMS, сервисы."),
]

# Подразделения — реальные кафедры университета по организационной
# структуре на 2026–2027 учебный год. Кафедры — учебное ядро,
# поэтому разбивка показателей идёт по ним.
DEPARTMENTS = [
    ("kaf-it",  "Кафедра «Информационные технологии и прикладная математика»"),
    ("kaf-uo",  "Кафедра «Управление организационными системами»"),
    ("kaf-psy", "Кафедра «Психология и управление персоналом»"),
    ("kaf-gum", "Кафедра «Гуманитарные дисциплины»"),
    ("kaf-gr",  "Кафедра «Гражданско-правовые дисциплины»"),
    ("kaf-eco", "Кафедра «Экономика, туризм и инновационные рыночные исследования»"),
]

# metric_id, block_id, title, unit, dir, target
METRICS = [
    ("plan-enroll",        "academic", "Выполнение плана приёма",            "%",       "higher", 90),
    ("programs",           "academic", "Действующие образовательные программы", "шт.",   "higher", 25),
    ("enroll-count",       "academic", "Приём на программы (год)",            "чел.",    "higher", 1800),
    ("continuent",         "academic", "Контингент обучающихся",              "чел.",    "higher", 21000),
    ("rostchislenie",      "academic", "Отчисления (год)",                     "чел.",    "lower",  700),
    ("procent-otchislenie", "academic", "Процент отчисления",                  "%",       "lower",  3.5),
    ("sredniy-ball",       "results",  "Средний балл ЕГЭ",                     "балл",    "higher", 70),
    ("uspevaemost",        "results",  "Успеваемость (без двоек)",             "%",       "higher", 90),
    ("kachestvo",          "results",  "Качество знаний (без троек)",          "%",       "higher", 65),
    ("zadolzhennosti",     "results",  "Доля задолженностей",                  "%",       "lower",  8),
    ("otlichniki",         "results",  "Доля отличников",                      "%",       "higher", 12),
    ("sga",                "results",  "Средний семестровый балл",              "балл",    "higher", 4.0),
    ("vnoko-employers",    "vnoko",    "Опрос работодателей",                  "%",       "higher", 85),
    ("vnoko-staff",        "vnoko",    "Опрос педагогических работников",       "%",       "higher", 85),
    ("vnoko-parents",      "vnoko",    "Опрос родителей",                      "%",       "higher", 85),
    ("vnoko-students",     "vnoko",    "Опрос обучающихся",                    "%",       "higher", 85),
    ("vnoko-vpr",          "vnoko",    "Средний балл ВПР (СПО)",               "балл",    "higher", 4.0),
    ("vnoko-planfact",     "vnoko",    "Доля ООП с опубликованным планом-фактом", "%", "higher", 95),
    ("granty",             "science",  "Объём грантов",                        "млн ₽",   "higher", 420),
    ("publ-scopus",        "science",  "Публикации Scopus/WoS",                "шт.",     "higher", 700),
    ("publ-rinc",          "science",  "Публикации РИНЦ",                      "шт.",     "higher", 1400),
    ("citiruemost",        "science",  "Цитируемость",                         "индекс",  "higher", 12),
    ("patenty",            "science",  "Патенты и РИД",                        "шт.",     "higher", 40),
    ("studcouncil",        "upr",      "Мероприятия студсовета",               "шт./год", "higher", 60),
    ("clinic",             "upr",      "Дела юридической клиники",             "шт.",     "higher", 40),
    ("career-centre",      "upr",      "Обращений в центр карьеры",             "тыс",     "higher", 12),
    ("vovlechennost",      "upr",      "Вовлечённость в воспитательные мероприятия", "%", "higher", 70),
    ("napolnyaemost",      "digital",  "Наполняемость аудиторий",              "%",       "higher", 75),
    ("nagruzka-pps",       "digital",  "Средняя нагрузка ППС",                 "час.",    "lower",  900),
    ("lms-activity",       "digital",  "Активность в LMS",                     "индекс",  "higher", 55),
    ("cifrovyye",          "digital",  "Цифровые сервисы (внедрённые)",       "шт.",     "higher", 30),
    ("eios",               "digital",  "Доля курсов в ЭИОС",                   "%",       "higher", 90),
]

# Образовательные программы (ФГОС). Источник — раздел «Образовательные
# стандарты и требования» на сайте университета. approved — год
# утверждения действующей редакции, updated — год последнего
# изменения. Численность обучающихся по программам на сайте не
# публикуется, поэтому здесь её нет.
STANDARDS = [
    ("09.03.03", "Бакалавриат", "Прикладная информатика", 2017, 2023),
    ("37.03.01", "Бакалавриат", "Психология", 2020, 2023),
    ("38.03.01", "Бакалавриат", "Экономика", 2020, 2023),
    ("38.03.02", "Бакалавриат", "Менеджмент", 2020, 2023),
    ("38.03.03", "Бакалавриат", "Управление персоналом", 2020, 2023),
    ("38.03.05", "Бакалавриат", "Бизнес-информатика", 2020, 2023),
    ("40.03.01", "Бакалавриат", "Юриспруденция", 2020, 2023),
    ("43.03.02", "Бакалавриат", "Туризм", 2017, 2023),
    ("09.04.03", "Магистратура", "Прикладная информатика", 2017, 2021),
    ("37.04.01", "Магистратура", "Психология", 2020, 2020),
    ("38.04.01", "Магистратура", "Экономика", 2020, 2020),
    ("38.04.02", "Магистратура", "Менеджмент", 2020, 2020),
    ("40.04.01", "Магистратура", "Юриспруденция", 2020, 2020),
    ("43.04.02", "Магистратура", "Туризм", 2017, 2021),
    ("09.02.07", "СПО", "Информационные системы и программирование", 2016, 2024),
    ("09.02.11", "СПО", "Разработка и управление программным обеспечением", 2025, 2025),
    ("09.02.12", "СПО", "Техническая эксплуатация и сопровождение информационных систем", 2025, 2025),
    ("31.02.02", "СПО", "Акушерское дело", 2022, 2026),
    ("31.02.06", "СПО", "Стоматология профилактическая", 2022, 2026),
    ("33.02.01", "СПО", "Фармация", 2021, 2026),
    ("34.02.01", "СПО", "Сестринское дело", 2022, 2026),
    ("38.02.01", "СПО", "Экономика и бухгалтерский учет (по отраслям)", 2024, 2024),
    ("38.02.03", "СПО", "Операционная деятельность в логистике", 2022, 2024),
    ("40.02.04", "СПО", "Юриспруденция", 2023, 2023),
    ("43.02.16", "СПО", "Туризм и гостеприимство", 2022, 2024),
]

# Синонимы из web/js/ai.js — тот же список, иначе вопрос «отсев»
# перестаёт находить метрику после переезда на сервер.
ALIASES = {
    "plan-enroll": ["план приема", "выполнение плана", "набор", "поступление"],
    "programs": ["образовательные программы", "ооп", "фгос", "действующие программы"],
    "enroll-count": ["прием на программы", "бюджетный прием", "численность абитуриентов"],
    "continuent": ["контингент", "численность студентов", "студентов", "обучающихся"],
    "rostchislenie": ["отчисления", "отсев", "выбытие", "отчисленные"],
    "procent-otchislenie": ["процент отчисления", "доля отсева", "отсев"],
    "sredniy-ball": ["средний балл", "егэ"],
    "uspevaemost": ["успеваемость", "без двоек"],
    "kachestvo": ["качество знаний", "качество"],
    "zadolzhennosti": ["задолженность", "академическая задолженность", "долги"],
    "otlichniki": ["отличники", "доля отличников"],
    "sga": ["средний семестровый балл", "сга", "успеваемость по семестрам"],
    "vnoko-employers": ["опрос работодателей", "работодатели", "удовлетворенность работодателей"],
    "vnoko-staff": ["опрос педагогических работников", "педагогические работники", "опрос преподавателей"],
    "vnoko-parents": ["опрос родителей", "родители", "удовлетворенность родителей"],
    "vnoko-students": ["опрос обучающихся", "опрос студентов", "удовлетворенность обучающихся"],
    "vnoko-vpr": ["впр", "всероссийские проверочные работы", "средний балл впр"],
    "vnoko-planfact": ["план-факт", "план факт", "опубликованный план-факт"],
    "granty": ["гранты", "объем грантов"],
    "publ-scopus": ["публикации scopus", "scopus", "wos", "публикации"],
    "publ-rinc": ["публикации ринц", "ринц"],
    "citiruemost": ["цитируемость"],
    "patenty": ["патенты", "рид", "интеллектуальная собственность"],
    "studcouncil": ["студсовет", "студенческий совет", "мероприятия студсовета"],
    "clinic": ["юридическая клиника", "клиника", "дела клиники"],
    "career-centre": ["центр карьеры", "карьера", "обращения в центр карьеры"],
    "vovlechennost": ["вовлеченность", "вовлечённость", "воспитательные мероприятия"],
    "napolnyaemost": ["наполняемость", "аудитории", "посещаемость"],
    "nagruzka-pps": ["нагрузка ппс", "нагрузка преподавателя"],
    "lms-activity": ["lms", "активность в lms", "moodle"],
    "cifrovyye": ["цифровые сервисы", "цифровизация"],
    "eios": ["эиос", "электронная информационно-образовательная среда", "курсы в эиос"],
}

MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
          "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]

LASTNAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Смирнов",
             "Волков", "Лебедев", "Козлов", "Новиков", "Морозов"]
FIRSTNAMES = ["Алексей", "Мария", "Дмитрий", "Анна", "Иван",
              "Елена", "Никита", "Ольга", "Сергей", "Наталья"]
REASONS = ["академическая задолженность", "неуспеваемость",
           "семейные обстоятельства", "перевод в другой вуз",
           "по собственному желанию", "непрохождение аттестации"]

# base / vol — параметры генератора, как в data.js
GEN = {
    "plan-enroll": (96, 8), "programs": (25, 0), "enroll-count": (1750, 90),
    "continuent": (20800, 220), "rostchislenie": (640, 70), "procent-otchislenie": (3.1, 0.4),
    "sredniy-ball": (74, 3), "uspevaemost": (94, 2), "kachestvo": (68, 3),
    "zadolzhennosti": (9.4, 1), "otlichniki": (13.5, 1.5), "sga": (4.1, 0.15),
    "vnoko-employers": (88, 2), "vnoko-staff": (86, 2), "vnoko-parents": (84, 2),
    "vnoko-students": (89, 2), "vnoko-vpr": (4.2, 0.1), "vnoko-planfact": (80, 3),
    "granty": (390, 25), "publ-scopus": (680, 40), "publ-rinc": (1080, 70),
    "citiruemost": (12.8, 0.8), "patenty": (38, 4),
    "studcouncil": (52, 5), "clinic": (34, 4), "career-centre": (11, 1),
    "vovlechennost": (66, 4),
    "napolnyaemost": (78, 4), "nagruzka-pps": (780, 60), "lms-activity": (62, 6),
    "cifrovyye": (20, 3), "eios": (85, 3),
}


def mulberry32(seed):
    """Побайтово тот же генератор, что в web/js/data.js.

    В JS все операции 32-битные со знаком, поэтому каждое
    промежуточное значение маскируется. Math.round в JS округляет
    к +Inf, а не как round() в Python (тот округляет к чётному) —
    это тоже учитывается в js_round.
    """
    state = {"a": seed & U32}

    def rnd():
        # a = (a + 0x6D2B79F5) | 0
        a = (state["a"] + 0x6D2B79F5) & U32
        state["a"] = a
        # t = Math.imul(a ^ (a >>> 15), 1 | a)
        t0 = ((a ^ (a >> 15)) * (1 | a)) & U32
        # t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
        t1 = ((t0 ^ (t0 >> 7)) * (61 | t0)) & U32
        t = ((t0 + t1) & U32) ^ t0
        # ((t ^ (t >>> 14)) >>> 0) / 2**32
        return ((t ^ (t >> 14)) & U32) / 4294967296.0

    return rnd


def js_round(x):
    """Math.round: к +Infinity. Python round() округляет к чётному — не то."""
    return math.floor(x + 0.5)


def make_series(metric_id, seed):
    base, vol = GEN[metric_id]
    rnd = mulberry32(seed)
    points = 12
    arr = []
    v = base * (0.9 + rnd() * 0.2)
    drift = (base - v) / points
    for _ in range(points):
        v += drift + (rnd() - 0.5) * vol * 0.5
        arr.append(max(0, js_round(v * 10) / 10))
    return arr


def make_drill(metric_id, seed, dept_name, dept_id, direction):
    base, _ = GEN[metric_id]
    rnd = mulberry32(seed)
    count = 5 + int(rnd() * 6)
    rows = []
    for i in range(count):
        fio = LASTNAMES[int(rnd() * len(LASTNAMES))] + " " + \
              FIRSTNAMES[int(rnd() * len(FIRSTNAMES))]
        if direction == "lower":
            value = js_round(base * (0.6 + rnd() * 0.8) * 10) / 10
        else:
            value = js_round(base * (0.85 + rnd() * 0.3) * 10) / 10
        reason = REASONS[int(rnd() * len(REASONS))]
        rows.append({"id": f"{dept_id}-{i}", "fio": fio, "dept": dept_name,
                     "value": value, "reason": reason})
    return rows


def compute_status(value, direction, target):
    if direction == "higher":
        score = 1.0 if target == 0 else value / target
    else:
        score = 1.0 if target == 0 else target / value
    if score >= 0.92:
        return "green"
    if score >= 0.8:
        return "yellow"
    return "red"


def build_demo(seed=config.DEMO_SEED):
    """Полный срез витрины — та же логика, что build() в data.js."""
    rnd = mulberry32(seed)
    year = 2026
    periods = [(f"{year}-{m:02d}", m) for m in range(1, 13)]

    metrics = []
    for i, (mid, block, title, unit, direction, target) in enumerate(METRICS):
        series = make_series(mid, seed + i * 101)
        current = series[-1]
        prev = series[-2] if len(series) > 1 else series[0]
        delta = ((current - prev) / prev * 100) if prev else 0.0
        base, _ = GEN[mid]

        # Доли подразделений обязаны в сумме давать итог по университету.
        # Раньше каждое подразделение получало base * (0.82...1.18)
        # независимо, и шесть таких значений давали примерно шестикратный
        # итог. Дефект был не виден, пока цифры показывались только общей
        # строкой; как только агенты стали считать разбивку по факультетам,
        # части перестали сходиться с целым.
        weights = [0.82 + rnd() * 0.36 for _ in DEPARTMENTS]
        wsum = sum(weights)
        dept_values = []
        for di, (dept_id, dept_name) in enumerate(DEPARTMENTS):
            v = current * (weights[di] / wsum)
            rounded = js_round(v * 10) / 10
            dept_values.append({
                "dept_id": dept_id, "dept_name": dept_name,
                "value": rounded,
                "share": (rounded / current * 100) if current else 0.0,
            })

        drill = {}
        for di, (dept_id, dept_name) in enumerate(DEPARTMENTS):
            drill[dept_id] = make_drill(mid, seed + i * 7 + di, dept_name,
                                        dept_id, direction)

        metrics.append({
            "metric_id": mid, "block_id": block, "title": title,
            "unit": unit, "direction": direction, "target": target,
            "series": series, "current": current, "prev": prev, "delta": delta,
            "status": compute_status(current, direction, target),
            "dept": dept_values, "drill": drill,
        })
    return {"periods": periods, "metrics": metrics}


def seed_metrics(conn, seed=config.DEMO_SEED, force=False):
    """Наполняет витрину демо-данными. Идемпотентно."""
    data = build_demo(seed)

    if force:
        conn.execute("DELETE FROM fact_metric")
        conn.execute("DELETE FROM fact_detail")
        conn.execute("DELETE FROM dim_period")
        conn.execute("DELETE FROM dim_metric")
        conn.execute("DELETE FROM dim_org")
        conn.execute("DELETE FROM metric_alias")
        conn.execute("DELETE FROM dim_standard")

    for i, (pid, mnum) in enumerate(data["periods"]):
        conn.execute(
            "INSERT OR REPLACE INTO dim_period(period_id, ord, label, year, month)"
            " VALUES(?,?,?,?,?)",
            (pid, i, MONTHS[mnum - 1], int(pid[:4]), mnum))
    for i, (oid, title) in enumerate(DEPARTMENTS):
        conn.execute("INSERT OR REPLACE INTO dim_org(org_id, title, ord) VALUES(?,?,?)",
                     (oid, title, i))
    for i, (mid, block, title, unit, direction, target) in enumerate(METRICS):
        conn.execute(
            "INSERT OR REPLACE INTO dim_metric"
            "(metric_id, block_id, title, unit, dir, target, ord) VALUES(?,?,?,?,?,?,?)",
            (mid, block, title, unit, direction, target, i))
        for alias in ALIASES.get(mid, []):
            conn.execute("INSERT OR REPLACE INTO metric_alias(metric_id, alias)"
                         " VALUES(?,?)", (mid, alias))
    for code, level, title, approved, updated in STANDARDS:
        conn.execute(
            "INSERT OR REPLACE INTO dim_standard"
            "(code, level, title, approved, updated) VALUES(?,?,?,?,?)",
            (code, level, title, approved, updated))

    last_period = data["periods"][-1][0]
    for i, m in enumerate(data["metrics"]):
        for pi, (pid, _mnum) in enumerate(data["periods"]):
            conn.execute(
                "INSERT OR REPLACE INTO fact_metric"
                "(period_id, metric_id, org_id, value, status) VALUES(?,?,?,?,?)",
                (pid, m["metric_id"], "ALL", m["series"][pi],
                 m["status"] if pid == last_period else None))
            for d in m["dept"]:
                if pid != last_period:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO fact_metric"
                    "(period_id, metric_id, org_id, value, status) VALUES(?,?,?,?,?)",
                    (pid, m["metric_id"], d["dept_id"], d["value"], m["status"]))

        for dept_id, rows in m["drill"].items():
            for r in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO fact_detail"
                    "(record_id, period_id, metric_id, org_id, subject_hash,"
                    " subject_label, value, reason) VALUES(?,?,?,?,?,?,?,?)",
                    (f"{m['metric_id']}:{last_period}:{dept_id}:{r['id']}",
                     last_period, m["metric_id"], dept_id,
                     None, r["fio"], r["value"], r["reason"]))

    conn.commit()
    set_meta(conn, "demo_seeded", "1")
    set_meta(conn, "demo_seed", str(seed))
    set_meta(conn, "schema_version", "1")
    set_meta(conn, "demo_data_notice", config.DEMO_BANNER)
    return len(data["metrics"])


def detect_blocks(rel_path, title, text=""):
    """К каким блокам дашборда относится документ. Может быть несколько.

    Нужно, чтобы поиск сужался до домена: иначе каждый вопрос
    обсуждает все документы и специалисты агентов ничем не отличаются
    друг от друга.

    Порядок: явные метки в файле важнее догадки по названию. Метки
    задаются во frontmatter markdown:
        ---
        block: finance
        blocks: academic, results
        ---
    Если их нет, смотрим название файла и заголовок документа.
    """
    head = (text or "")[:600]
    found = []

    if head.startswith("---"):
        parts = head.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip().lower()
                vals = value.replace(",", " ").split()
                if key in ("block", "blocks"):
                    for v in vals:
                        v = v.strip().lower()
                        if v in BLOCK_IDS and v not in found:
                            found.append(v)
    if found:
        return found

    haystack = " ".join([rel_path.lower(), (title or "").lower()])
    for block_id, words in BLOCK_KEYWORDS.items():
        if any(word in haystack for word in words):
            found.append(block_id)
    if found:
        return found

    # Запасной круг: смотрим начало текста. Протокол учёного совета или
    # стратегия относятся сразу к нескольким блокам, и по названию это
    # не определить. Ограничение по числу блоков и по порогу совпадений
    # нужно, чтобы в блок не попал документ, где одно случайное слово
    # совпало с ключевым.
    body = (text or "")[:2500].lower()
    if not body.strip():
        return found
    scored = []
    for block_id, words in BLOCK_KEYWORDS.items():
        hits = sum(1 for w in words if w in body)
        if hits:
            scored.append((hits, block_id))
    scored.sort(reverse=True)
    best = scored[0][0] if scored else 0
    for hits, block_id in scored[:3]:
        if hits >= max(1, best // 2):
            found.append(block_id)
    return found


BLOCK_IDS = {b[0] for b in BLOCKS}

# Ключевые слова в названии файла и заголовке. Порядок блоков важен:
# «трудоустройство» проверяется раньше «репутации», потому что отчёт
# о трудоустройстве упоминает и удовлетворённость, и медиа.
BLOCK_KEYWORDS = {
    "academic": ["priem", "priёm", "приём", "prijem", "контингент", "otchisl",
                 "отчисл", "восстановл", "admission", "enrol", "образовательные программы",
                 "ооп", "фгос"],
    "results": ["attestaci", "аттестац", "rezultat", "результатив", "успеваем",
                "zadolzhen", "задолжен", "качество знаний", "учебн", "егэ", "сга"],
    "vnoko": ["vnoko", "внoco", "вноко", "независимая оценка", "опрос",
              "план-факт", "план факт", "впр", "самообследован", "качество образования"],
    "science": ["nau", "научн", "grant", "грант", "publikac", "публикац",
                "scopus", "ринц", "patent", "патент", "исследован", "метакомпетенц",
                "нил", "цитируемость"],
    "upr": ["воспитан", "studсовет", "студсовет", "молодежн",
            "юридическая клиника", "клиника", "центр карьеры", "карьер",
            "социализац", "вовлеченн"],
    "digital": ["nagruzk", "нагрузк", "lms", "moodle", "auditor", "аудитор",
               "cifrov", "цифров", "obespech", "цифровизац", "эиос",
               "электронная информационно-образовательная среда"],
}


def load_vitrine(conn):
    """Витрина в том же виде, в котором ждёт её web/js/app.js.

    Имена полей (id, name, deptId, deptName) намеренно совпадают
    с data.js: интерфейс написан под них, и переименование здесь
    означало бы правку всего фронтенда ради хранения данных.
    """
    periods = [{"period_id": r["period_id"], "label": r["label"]}
               for r in conn.execute(
                   "SELECT period_id, label FROM dim_period ORDER BY ord")]
    orgs = [{"id": r["org_id"], "org_id": r["org_id"],
             "name": r["title"], "title": r["title"]}
            for r in conn.execute("SELECT org_id, title FROM dim_org ORDER BY ord")]
    blocks = [{"id": b[0], "title": b[1], "icon": b[2], "role": b[3], "desc": b[4]}
              for b in BLOCKS]

    labels = [p["label"] for p in periods]
    metrics = []
    for row in conn.execute(
            "SELECT * FROM dim_metric ORDER BY ord"):
        mid = row["metric_id"]
        series = [r["value"] for r in conn.execute(
            "SELECT value FROM fact_metric WHERE metric_id=? AND org_id='ALL'"
            " ORDER BY period_id", (mid,))]
        dept = [{"deptId": r["org_id"], "deptName": r["title"], "value": r["value"],
                 "share": 0.0}
                for r in conn.execute(
                    "SELECT f.org_id, d.title, f.value FROM fact_metric f"
                    " JOIN dim_org d ON d.org_id = f.org_id"
                    " WHERE f.metric_id=? AND f.org_id<>'ALL'"
                    " ORDER BY d.ord", (mid,))]
        current = series[-1] if series else 0
        for dd in dept:
            dd["share"] = (dd["value"] / current * 100) if current else 0.0

        drill = {}
        for r in conn.execute(
                "SELECT org_id, record_id, subject_label, value, reason"
                " FROM fact_detail WHERE metric_id=?", (mid,)):
            drill.setdefault(r["org_id"], []).append({
                "id": r["record_id"], "fio": r["subject_label"],
                "dept": next((o["name"] for o in orgs if o["org_id"] == r["org_id"]), ""),
                "value": r["value"], "reason": r["reason"]})

        prev = series[-2] if len(series) > 1 else (series[0] if series else 0)
        metrics.append({
            "id": mid, "block": row["block_id"], "title": row["title"],
            "unit": row["unit"], "dir": row["dir"], "target": row["target"],
            "series": series, "current": current, "prev": prev,
            "delta": ((current - prev) / prev * 100) if prev else 0.0,
            "status": compute_status(current, row["dir"], row["target"]),
            "dept": dept, "drill": drill,
        })

    standards = [{"code": r["code"], "level": r["level"], "title": r["title"],
                  "approved": r["approved"], "updated": r["updated"]}
                 for r in conn.execute(
                     "SELECT code, level, title, approved, updated"
                     " FROM dim_standard ORDER BY code")]

    return {"blocks": blocks, "metrics": metrics, "dept": orgs,
            "standards": standards,
            "labels": labels, "periods": periods, "demo": True}


if __name__ == "__main__":
    c = connect()
    init_db(c)
    n = seed_metrics(c, force="--force" in __import__("sys").argv)
    print(f"витрина наполнена: {n} метрик")
    print("периодов:", c.execute("SELECT COUNT(*) FROM dim_period").fetchone()[0])
    print("фактов:", c.execute("SELECT COUNT(*) FROM fact_metric").fetchone()[0])
    print("записей drill-down:", c.execute("SELECT COUNT(*) FROM fact_detail").fetchone()[0])
    c.close()