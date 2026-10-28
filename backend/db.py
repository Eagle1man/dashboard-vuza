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
  -- 'official' — число взято из документа университета,
  -- 'demo'     — демонстрационное, историю за ним нет.
  source    TEXT DEFAULT 'demo',
  -- Год и название документа-источника для официальных чисел.
  src_note  TEXT,
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
  org_id    TEXT PRIMARY KEY,
  title     TEXT NOT NULL,
  parent_id TEXT,           -- академия, которой подчинена кафедра
  kind      TEXT NOT NULL,  -- 'academy' | 'dept'
  ord       INTEGER
);
-- Индекс по parent_id создаётся отдельно, ПОСЛЕ миграции: на старой
-- базе колонки parent_id ещё нет, и executescript упал бы на CREATE
-- INDEX раньше, чем _migrate успела её добавить.

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

-- 5. Загрузка данных подразделениями -----------------------------------
CREATE TABLE IF NOT EXISTS division (
  division_id TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  login       TEXT UNIQUE NOT NULL,
  pass_hash   TEXT NOT NULL,
  contact     TEXT,
  created_at  TEXT
);
CREATE INDEX IF NOT EXISTS division_login ON division(login);

CREATE TABLE IF NOT EXISTS upload (
  upload_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  division_id TEXT NOT NULL REFERENCES division(division_id),
  doc_id      INTEGER REFERENCES doc(doc_id) ON DELETE SET NULL,
  filename    TEXT NOT NULL,
  rel_path    TEXT NOT NULL,
  sha256      TEXT NOT NULL,
  size_bytes  INTEGER NOT NULL,
  uploaded_at TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'ok',   -- ok | error | duplicate
  error       TEXT
);
CREATE INDEX IF NOT EXISTS upload_division ON upload(division_id);
CREATE INDEX IF NOT EXISTS upload_sha ON upload(sha256);
CREATE INDEX IF NOT EXISTS upload_status ON upload(status);

CREATE TABLE IF NOT EXISTS doc_meta (
  doc_id         INTEGER PRIMARY KEY REFERENCES doc(doc_id) ON DELETE CASCADE,
  upload_id      INTEGER REFERENCES upload(upload_id),
  division_id    TEXT NOT NULL REFERENCES division(division_id),
  title          TEXT NOT NULL,
  purpose        TEXT NOT NULL,      -- зачем нужны эти данные / какое решение принимают
  period         TEXT NOT NULL,      -- за какой период (квартал / семестр / год / дата среза)
  frequency      TEXT NOT NULL,      -- разово | ежемесячно | ежеквартально | раз в семестр | ежегодно
  responsible    TEXT NOT NULL,      -- ответственный (ФИО + контакт)
  access_level   TEXT NOT NULL DEFAULT 'open',  -- open | internal | confidential
  next_expected  TEXT,               -- дата, когда ожидается следующая загрузка
  metric_ids     TEXT DEFAULT '[]',   -- JSON-массив id карточек дашборда, которые кормит этот документ
  confirmed      INTEGER DEFAULT 0,   -- ответственный подтвердил числа
  uploaded_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_meta_division ON doc_meta(division_id);
CREATE INDEX IF NOT EXISTS doc_meta_next_expected ON doc_meta(next_expected);

CREATE TABLE IF NOT EXISTS metric_source (
  metric_id TEXT NOT NULL,
  doc_id    INTEGER NOT NULL REFERENCES doc(doc_id) ON DELETE CASCADE,
  confirmed INTEGER DEFAULT 0,
  PRIMARY KEY (metric_id, doc_id)
);
CREATE INDEX IF NOT EXISTS metric_source_metric ON metric_source(metric_id);

-- 6. Конвейер разбора: сырое содержимое, карточка, проверки, решение ----
--
-- Правило ТЗ: сырое содержимое файла хранится отдельно и НЕ перезаписывается
-- при исправлении. Цепочка «сырое значение → нормализованное → витрина»
-- восстанавливается из upload_raw + parse_card + fact_provenance.
CREATE TABLE IF NOT EXISTS upload_raw (
  raw_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  upload_id INTEGER NOT NULL REFERENCES upload(upload_id) ON DELETE CASCADE,
  kind      TEXT NOT NULL,      -- page | block | cell | row
  label     TEXT,               -- 'стр. 12' | 'лист/строка/адрес'
  text      TEXT,
  page_no   INTEGER,
  sheet     TEXT,
  row_no    INTEGER,
  col_no    INTEGER,
  cell_ref  TEXT,
  seq       INTEGER
);
CREATE INDEX IF NOT EXISTS upload_raw_upload ON upload_raw(upload_id);

-- Карточка разбора: что модель предложила (тип, период, сопоставления).
-- `values`/`mapping`/`unmapped` — JSON-массивы, форма проверяется в Python.
CREATE TABLE IF NOT EXISTS parse_card (
  upload_id   INTEGER PRIMARY KEY REFERENCES upload(upload_id) ON DELETE CASCADE,
  doc_type    TEXT,                    -- предложенный тип документа
  division_id TEXT,
  period      TEXT,                    -- как период записан в файле
  period_id   TEXT,                    -- сопоставленный период витрины (или NULL)
  values_json TEXT NOT NULL DEFAULT '[]',
  mapping_json TEXT NOT NULL DEFAULT '[]',
  unmapped_json TEXT NOT NULL DEFAULT '[]',
  status      TEXT NOT NULL DEFAULT 'draft',   -- draft | corrected
  confidence  REAL,
  model       TEXT,                    -- роль+имя модели, например 'small:qwen2.5:1.5b'
  attempts    INTEGER DEFAULT 1,
  parse_ms    INTEGER,
  parse_error TEXT,
  edited_at   TEXT,
  edited_by   TEXT,
  -- Объяснение проверок большой моделью (ТЗ §13): хранится здесь же,
  -- чтобы экран проверки не пересчитывал его при каждом открытии.
  explanation   TEXT,
  expl_model    TEXT,
  expl_ms       INTEGER,
  explained_at  TEXT
);

-- Результаты детерминированных проверок (ТЗ §10). severity: warning | error.
CREATE TABLE IF NOT EXISTS upload_check (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  upload_id  INTEGER NOT NULL REFERENCES upload(upload_id) ON DELETE CASCADE,
  check_code TEXT NOT NULL,
  severity   TEXT NOT NULL,
  message    TEXT NOT NULL,
  field      TEXT,
  value_a    TEXT,
  value_b    TEXT
);
CREATE INDEX IF NOT EXISTS upload_check_upload ON upload_check(upload_id);

-- Решение человека по загрузке. Может быть несколько строк на загрузку
-- (reject → загрузка не публикуется; повторное решение идемпотентно —
-- оно апдейтит провенанс, а не плодит дубли).
CREATE TABLE IF NOT EXISTS upload_decision (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  upload_id  INTEGER NOT NULL REFERENCES upload(upload_id) ON DELETE CASCADE,
  actor      TEXT NOT NULL,
  action     TEXT NOT NULL,          -- accept | reject
  comment    TEXT,
  decided_at TEXT NOT NULL,
  published  INTEGER DEFAULT 0       -- сколько значений ушло в витрину
);
CREATE INDEX IF NOT EXISTS upload_decision_upload ON upload_decision(upload_id);

-- Провенанс каждого опубликованного значения (ТЗ §5.5).
-- UNIQUE по (период, метрика, орг, загрузка): повторное «принять» той же
-- загрузки обновляет строку, дублей не появляется.
CREATE TABLE IF NOT EXISTS fact_provenance (
  prov_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  period_id         TEXT NOT NULL,
  metric_id         TEXT NOT NULL,
  org_id            TEXT NOT NULL,
  source_upload_id  INTEGER REFERENCES upload(upload_id) ON DELETE SET NULL,
  source_doc_id     INTEGER REFERENCES doc(doc_id) ON DELETE SET NULL,
  source_file_name  TEXT,
  sha256            TEXT,
  extracted_value   TEXT,             -- как число записано в источнике
  value_exact       TEXT,             -- каноническое десятичное представление
  extraction_method TEXT,             -- xlsx_cell | pdf_page | docx_block | text_block
  parser_version    TEXT,
  parser_model      TEXT,
  confirmed_by      TEXT,
  confirmed_at      TEXT,
  note              TEXT,
  UNIQUE (period_id, metric_id, org_id, source_upload_id)
);
CREATE INDEX IF NOT EXISTS fact_provenance_metric ON fact_provenance(metric_id, period_id, org_id);
CREATE INDEX IF NOT EXISTS fact_provenance_upload ON fact_provenance(source_upload_id);

-- 7. Журнал событий аудита (ТЗ §11): кто и что сделал.
CREATE TABLE IF NOT EXISTS audit_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL,
  actor  TEXT,                 -- логин
  role   TEXT,                 -- division_id
  action TEXT NOT NULL,        -- login | login_fail | logout | upload | decide | publish | reindex | export
  entity TEXT,                 -- 'upload:12', 'metric:contingent'
  detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS audit_log_action ON audit_log(action, ts);

-- Попытки входа — для ограничения числа неудач (ТЗ §12).
CREATE TABLE IF NOT EXISTS login_attempt (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  ts    TEXT NOT NULL,
  login TEXT,
  ip    TEXT,
  ok    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS login_attempt_login ON login_attempt(login, ts);

-- Трассировка вызовов LLM (ТЗ §13): роль, модель, длительность, статус.
-- Текст документов и ответы модели здесь НЕ хранятся — только метаданные.
CREATE TABLE IF NOT EXISTS llm_trace (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  purpose     TEXT NOT NULL,    -- parse_card | consistency | ask | test
  role        TEXT NOT NULL,    -- small | large
  model       TEXT,
  resolved    TEXT,             -- env | meta | auto | fixed
  duration_ms INTEGER,
  ok          INTEGER DEFAULT 0,
  error_code  TEXT,
  prompt_chars INTEGER,
  response_chars INTEGER
);
CREATE INDEX IF NOT EXISTS llm_trace_ts ON llm_trace(ts);
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
    # Ожидание блокировки: без него параллельный запрос (страница + загрузка)
    # при WAL падает сразу, вместо того чтобы дождаться освобождения записи.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn=None):
    own = conn is None
    conn = conn or connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    # Индексы по добавленным колонкам — после миграции, иначе на
    # старой базе CREATE INDEX упадёт на ещё не существующем поле.
    conn.executescript(POST_INDEXES)
    conn.commit()
    if own:
        conn.close()
    return True


POST_INDEXES = """
CREATE INDEX IF NOT EXISTS dim_org_parent ON dim_org(parent_id);
"""


def _migrate(conn):
    """Мелкие правки схемы для уже созданных баз.

    Отдельная функция нужна потому, что CREATE TABLE IF NOT EXISTS не
    добавляет колонку в существующую таблицу: на старой базе установки
    новый столбец просто не появился бы, и документы молча получили бы
    block_id = NULL, а специалисты агентов потеряли бы фильтр.
    """
    # Таблицы, которых нет на этой базе (новая схема ещё не создана),
    # миграция пропускает: ALTER по несуществующей таблице падает
    # целиком и оставляет базу без всех остальных правок.
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, column, decl in (
        ("doc", "block_id", "TEXT"),
        ("dim_org", "parent_id", "TEXT"),
        ("dim_org", "kind", "TEXT"),
        ("dim_metric", "source", "TEXT DEFAULT 'demo'"),
        ("dim_metric", "src_note", "TEXT"),
        # Конвейер загрузки (ТЗ 2.0): исходное имя, найденный формат,
        # кто загрузил, текст ошибки без путей сервера.
        ("upload", "original_name", "TEXT"),
        ("upload", "mime_detected", "TEXT"),
        ("upload", "extension", "TEXT"),
        ("upload", "uploaded_by", "TEXT"),
        ("upload", "note", "TEXT"),
        ("upload", "error_code", "TEXT"),
        ("upload", "error_message", "TEXT"),
        ("upload", "parsed_at", "TEXT"),
        ("upload", "decided_at", "TEXT"),
        # Точное десятичное представление: REAL удобен для графиков,
        # но денежные значения им округляются. value_exact хранит
        # каноническую десятичную строку как источник истины.
        ("fact_metric", "value_exact", "TEXT"),
        # Каталог показателей: диапазоны и допуск заполняет владелец
        # каталога (ТЗ §19). NULL = не задано, проверка не выполняется.
        ("dim_metric", "min_val", "REAL"),
        ("dim_metric", "max_val", "REAL"),
        ("dim_metric", "tolerance", "REAL"),
        ("dim_metric", "periodicity", "TEXT"),
        # Объяснение проверок большой моделью (ТЗ §13). Колонок нет в
        # базах, где таблица parse_card успела появиться до того, как
        # в схему добавили explanation: CREATE TABLE IF NOT EXISTS
        # существующую таблицу не дописывает, а кэш объяснения в старой
        # базе падал бы с "no such column".
        ("parse_card", "explanation", "TEXT"),
        ("parse_card", "expl_model", "TEXT"),
        ("parse_card", "expl_ms", "INTEGER"),
        ("parse_card", "explained_at", "TEXT"),
    ):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if table in existing and column not in cols:
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
     "Успеваемость, качество знаний, трудоустройство выпускников."),
    ("finance", "Финансово-хозяйственная деятельность", "💰",
     ["rector", "admin"],
     "План ФХД: доходы, расходы, фонд оплаты труда, структура затрат."),
    ("vnoko", "Качество образования (ВНОКО)", "✅",
     ["rector", "prorector", "admin"],
     "Внутренняя независимая оценка качества: опросы и план-факт."),
    ("science", "Наука и метакомпетенции", "🔬",
     ["rector", "prorector", "admin"],
     "НИР, публикации, интеллектуальная собственность."),
    ("upr", "Воспитательная работа и молодёжная политика", "🤝",
     ["rector", "prorector", "admin"],
     "Студенческий совет, юридическая клиника, центр карьеры."),
    ("digital", "Цифровые сервисы и ЭИОС", "💻",
     ["rector", "prorector", "admin"],
     "Электронная информационно-образовательная среда, LMS, сервисы."),
]

# Структура подразделений — официальная.
#
# Три источника, и они не совпадают между собой:
#
# 1. Организационная структура на 2026–2027 учебный год (приложение
#    1 к приказу) — 5 академий и 9 кафедр. Это действующий документ
#    текущего учебного года, и именно он задаёт состав.
# 2. Отчёт о самообследовании на 01.04.2026 (стр. 10) — на 1 апреля
#    2026 академий было тоже пять, но назывались они иначе, а кафедр
#    было десять: к двум нынешним при тогдашнем составе добавлялись
#    «Физическое воспитание» и «Экономика и инновационные рыночные
#    исследования». Обеих в структуре 2026–2027 уже нет.
# 3. Страница «Академии» на сайте университета
#    (iubip.ru/university_ecosystem/academies/) — даёт состав и
#    принадлежность кафедр академиям. На ней ещё стоит «Физическое
#    воспитание»: страница не обновлена под текущий приказ, поэтому
#    за составом кафедр идём по приказу, а за принадлежностью — по
#    сайту, где она указана прямо.
#
# Итог: 5 академий, 9 кафедр. Академия профессионального образования
# кафедр из этого списка не имеет — она про среднее профессиональное
# образование, и оставлять её в сводке надо, иначе структура
# показывается неполной.
#
# (org_id, название, родитель, порядок, вид)
ORGS = [
    ("ak-econ",    "Академия экономики, туризма и индустрии гостеприимства", None, 0, "academy"),
    ("ak-digital", "Академия управления и цифрового развития",               None, 1, "academy"),
    ("ak-law",     "Академия права и государственной службы",                None, 2, "academy"),
    ("ak-hum",     "Академия гуманитарных технологий",                       None, 3, "academy"),
    ("ak-prof",    "Академия профессионального образования",                None, 4, "academy"),
    ("kaf-fin",  "Кафедра «Финансы, бухучет и налогообложение»",          "ak-econ",    0, "dept"),
    ("kaf-tour", "Кафедра «Экономика, туризм и инновационные рыночные исследования»", "ak-econ", 1, "dept"),
    ("kaf-uo",   "Кафедра «Управление организационными системами»",       "ak-digital", 0, "dept"),
    ("kaf-it",   "Кафедра «Информационные технологии и прикладная математика»", "ak-digital", 1, "dept"),
    ("kaf-gp",   "Кафедра «Государственно-правовые дисциплины»",           "ak-law",     0, "dept"),
    ("kaf-gr",   "Кафедра «Гражданско-правовые дисциплины»",               "ak-law",     1, "dept"),
    ("kaf-up",   "Кафедра «Уголовно-правовые дисциплины»",                "ak-law",     2, "dept"),
    ("kaf-psy",  "Кафедра «Психология и управление персоналом»",          "ak-hum",     0, "dept"),
    ("kaf-hum",  "Кафедра «Гуманитарные дисциплины»",                      "ak-hum",     1, "dept"),
]

# Разбивка показателей идёт по кафедрам — это учебное ядро, у каждого
# есть своя программа. Академии — верхний уровень сводки.
DEPTS = [o for o in ORGS if o[4] == "dept"]
ACADEMIES = [o for o in ORGS if o[4] == "academy"]

# metric_id, block_id, title, unit, dir, target
# target = None — целевого порога в документах нет, статус не считается.
METRICS = [
    # --- Академическая политика: числа из отчёта о самообследовании ---
    ("continuer-total",   "academic", "Контингент обучающихся",        "чел.", "higher", None),
    ("continuer-spo",     "academic", "Контингент СПО",                "чел.", "higher", None),
    ("continuer-ba",      "academic", "Контингент бакалавриата",        "чел.", "higher", None),
    ("continuer-ma",      "academic", "Контингент магистратуры",        "чел.", "higher", None),
    ("continuer-aspir",   "academic", "Аспиранты",                     "чел.", "higher", None),
    ("applicant-2025",    "academic", "Принято на 1 курс (2025)",      "чел.", "higher", None),
    ("graduate-2025",     "academic", "Выпуск 2025",                   "чел.", "higher", None),
    ("foreign",           "academic", "Иностранцы",                    "чел.", "higher", None),
    ("programs",          "academic", "Действующие образовательные программы", "шт.", "higher", None),
    # демо — по приёму целевого порога в документах нет
    ("plan-enroll",       "academic", "Выполнение плана приёма",       "%",    "higher", 90),

    # --- Учебные результаты ---
    ("trudoustroystvo",   "results",  "Трудоустройство выпускников",   "%",    "higher", None),
    ("sredniy-ball",      "results",  "Средний балл ЕГЭ",              "балл", "higher", 70),
    ("uspevaemost",       "results",  "Успеваемость (без двоек)",      "%",    "higher", 90),
    ("kachestvo",         "results",  "Качество знаний (без троек)",   "%",    "higher", 65),
    ("zadolzhennosti",    "results",  "Доля задолженностей",           "%",    "lower",  8),
    ("otlichniki",        "results",  "Доля отличников",               "%",    "higher", 12),
    ("sga",               "results",  "Средний семестровый балл",       "балл", "higher", 4.0),

    # --- Финансово-хозяйственная деятельность: план ФХД на 2026 год ---
    ("income",            "finance",  "Доходы (план 2026)",                 "тыс ₽", "higher", None),
    ("expense",           "finance",  "Расходы (план 2026)",                "тыс ₽", "lower",  None),
    ("income-edu",        "finance",  "Доходы от образовательных услуг",    "тыс ₽", "higher", None),
    ("subsidy",           "finance",  "Субсидия на образование",           "тыс ₽", "higher", None),
    ("fot",               "finance",  "Фонд оплаты труда",                  "тыс ₽", "lower",  None),
    ("fot-share",         "finance",  "Доля ФОТ в расходах",                "%",     "higher", None),
    ("arenda-share",      "finance",  "Доля аренды в расходах",             "%",     "lower",  None),
    ("subsidy-share",     "finance",  "Доля субсидии в доходах",            "%",     "higher", None),

    # --- Качество образования (ВНОКО) ---
    ("vnoko-employers",   "vnoko", "Опрос работодателей",                  "%",    "higher", 85),
    ("vnoko-staff",       "vnoko", "Опрос педагогических работников",       "%",    "higher", 85),
    ("vnoko-parents",     "vnoko", "Опрос родителей",                      "%",    "higher", 85),
    ("vnoko-students",    "vnoko", "Опрос обучающихся",                    "%",    "higher", 85),
    ("vnoko-vpr",         "vnoko", "Средний балл ВПР (СПО)",               "балл", "higher", 4.0),
    ("vnoko-planfact",    "vnoko", "Доля ООП с опубликованным планом-фактом", "%", "higher", 95),

    # --- Наука ---
    ("nir",               "science", "Объём НИР за 2025",               "млн ₽",  "higher", None),
    ("biblioteka",        "science", "Библиотечный фонд",              "экз.",    "higher", None),
    ("granty",            "science", "Объём грантов",                   "млн ₽",   "higher", 420),
    ("publ-scopus",       "science", "Публикации Scopus/WoS",           "шт.",     "higher", 700),
    ("publ-rinc",         "science", "Публикации РИНЦ",                 "шт.",     "higher", 1400),
    ("citiruemost",       "science", "Цитируемость",                    "индекс",  "higher", 12),
    ("patenty",           "science", "Патенты и РИД",                   "шт.",     "higher", 40),

    # --- Воспитательная работа и молодёжная политика ---
    ("studcouncil",       "upr", "Мероприятия студсовета",               "шт./год", "higher", 60),
    ("clinic",            "upr", "Дела юридической клиники",             "шт.",     "higher", 40),
    ("career-centre",     "upr", "Обращений в центр карьеры",             "тыс",     "higher", 12),
    ("vovlechennost",     "upr", "Вовлечённость в воспитательные мероприятия", "%", "higher", 70),

    # --- Цифровые сервисы и ЭИОС ---
    ("napolnyaemost",     "digital", "Наполняемость аудиторий",         "%",    "higher", 75),
    ("nagruzka-pps",      "digital", "Средняя нагрузка ППС",            "час.", "lower",  900),
    ("lms-activity",      "digital", "Активность в LMS",                "индекс", "higher", 55),
    ("cifrovyye",         "digital", "Цифровые сервисы (внедрённые)",  "шт.",   "higher", 30),
    ("eios",              "digital", "Доля курсов в ЭИОС",              "%",     "higher", 90),
]

# Официальные значения: метрика -> (значение, источник).
# У них НЕТ исторического ряда — документ даёт одну точку. Генерировать
# к ней прошлое нельзя: ректор увидит ровный график и примет его за
# историю. Поэтому у таких метрик ряд состоит из одного значения, а
# интерфейс показывает «исторического ряда нет».
SRC_SELF = "Отчёт о самообследовании на 01.04.2026"
SRC_FIN = "План ФХД на 2026 год"
OFFICIAL = {
    "continuer-total": (2946.0, SRC_SELF + ", стр. 13, табл. 2.2: всего обучающихся на 01.04.2026"),
    "continuer-spo":   (1328.0, SRC_SELF + ", стр. 13, табл. 2.2: СПО"),
    "continuer-ba":    (1145.0, SRC_SELF + ", стр. 14, табл. 2.2: бакалавриат"),
    "continuer-ma":    (443.0,  SRC_SELF + ", стр. 14, табл. 2.2: магистратура"),
    "continuer-aspir": (30.0,   SRC_SELF + ", стр. 14, табл. 2.2: подготовка научных и научно-педагогических кадров"),
    "applicant-2025":  (1245.0, SRC_SELF + ", стр. 13, табл. 2.1: принято на 1 курс (на 01.10.2025)"),
    "graduate-2025":   (888.0,  SRC_SELF + ", стр. 14, табл. 2.3: выпуск 2025 года, итого"),
    # Всего иностранных — 55, а не 37: 37 это бакалавриат (35) плюс
    # магистратура (2), а ещё 18 студентов в СПО. Итоговая строка
    # таблицы 2.2 — 55.
    "foreign":         (55.0,   SRC_SELF + ", стр. 13, табл. 2.2: в том числе иностранных студентов"),
    "programs":        (25.0,   "Сайт университета, раздел «Образовательные стандарты и требования»: 25 ФГОС"),
    "trudoustroystvo": (90.0,   SRC_SELF + ", стр. 15: «показатель составляет более 90 %» — 90 % указано как нижняя граница"),
    "income":          (221475.08, SRC_FIN + ", доходная часть, всего"),
    "expense":         (221475.08, SRC_FIN + ", расходная часть, всего"),
    "income-edu":      (184447.27, SRC_FIN + ", доходная часть, строка 1: образовательные услуги"),
    "subsidy":         (8287.30, SRC_FIN + ", доходная часть, строка 4: субсидия"),
    "fot":             (89900.00, SRC_FIN + ", расходная часть, строка 1: фонд оплаты труда"),
    "fot-share":       (40.6,    SRC_FIN + ", расчёт: 89900 / 221475,08"),
    "arenda-share":    (15.9,    SRC_FIN + ", расчёт: 35160 / 221475,08"),
    "subsidy-share":   (3.7,     SRC_FIN + ", расчёт: 8287,30 / 221475,08"),
    "nir":             (18.0,    SRC_SELF + ", стр. 17: «объём научных исследований за 2025 год составил 18 млн руб.»"),
    "biblioteka":      (84700.0, SRC_SELF + ", стр. 14: библиотечный фонд на 01.01.2026, печатных экземпляров"),
}

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
    "continuer-total": ["контингент", "численность обучающихся",
                        "сколько обучается", "сколько человек учится",
                        "общее число обучающихся", "сколько студентов"],
    # Корень «студент» здесь убираем осознанно: он попадает в вопрос
    # «успеваемость студентов», даёт ничью по весу с uspevaemost и
    # ломает выбор метрики. Побеждает тот, у кого найдено больше слов,
    # а на «студентах» оценивается любая из метрик одинаково.
    "continuer-spo":   ["колледж", "среднее профессиональное образование",
                        "численность СПО", "сколько в колледже"],
    "continuer-ba":    ["бакалавриат", "бакалавры", "численность бакалавриата",
                        "сколько на бакалавриате"],
    "continuer-ma":    ["магистратура", "магистранты", "численность магистратуры",
                        "сколько в магистратуре"],
    "continuer-ba":    ["бакалавриат", "бакалавры", "численность бакалавриата"],
    "continuer-ma":    ["магистратура", "магистранты", "численность магистратуры"],
    "continuer-aspir": ["аспиранты", "аспирантура", "численность аспирантов"],
    "applicant-2025":  ["прием", "принято", "набор", "поступление", "абитуриенты",
                        "сколько поступило"],
    "graduate-2025":   ["выпуск", "выпускники", "выпустилось", "окончили"],
    "foreign":         ["иностранцы", "иностранных", "граждане других стран"],
    "programs":        ["образовательные программы", "ооп", "фгос", "действующие программы"],
    "plan-enroll":     ["план приема", "выполнение плана"],
    "trudoustroystvo": ["трудоустройство", "трудоустроены", "трудоустроенных"],
    "sredniy-ball":    ["средний балл", "ЕГЭ"],
    "uspevaemost":     ["успеваемость", "без двоек"],
    "kachestvo":       ["качество знаний", "качество"],
    "zadolzhennosti":  ["задолженность", "долги"],
    "otlichniki":      ["отличники", "доля отличников"],
    "sga":             ["средний семестровый балл", "сга", "успеваемость по семестрам"],
    "income":          ["доходы", "доход", "выручка", "поступления"],
    "expense":         ["расходы", "затраты", "расход"],
    "income-edu":      ["доходы от образовательных услуг", "доходы от услуг", "платное обучение"],
    "subsidy":         ["субсидия", "субсидии", "бюджетное финансирование"],
    "fot":             ["фонд оплаты труда", "ФОТ", "зарплата", "зп"],
    "fot-share":       ["доля фот", "доля фонда оплаты труда"],
    "arenda-share":    ["аренда", "доля аренды"],
    "subsidy-share":   ["доля субсидии", "доля бюджета"],
    "nir":             ["НИР", "научные исследования", "объём исследований", "финансирование науки"],
    "biblioteka":      ["библиотека", "библиотечный фонд", "книжный фонд"],
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

# Подразделения, которые загружают данные для дашборда.
# Состав берём из организационной структуры на 2026–2027 учебный год
# (Orgstruktura_2026.pdf, Приложение 1 к приказу) — официальный документ
# университета. «Другое» — для подразделений, которых ещё нет в списке:
# админ добавит в справочник по мере необходимости.
DIVISIONS = [
    ("buhgalteria",   "Бухгалтерия",                          "Исполняющий обязанности директора по экономике и финансам", None),
    ("finoecon",      "Финансово-экономический отдел",        "Исполняющий обязанности директора по экономике и финансам", None),
    ("acad_director", "Академическая дирекция",               "Директор академической дирекции", None),
    ("umo",           "Учебно-методический отдел",            "Заведующий УМО", None),
    ("admission",     "Приёмная комиссия",                    "Председатель приёмной комиссии", None),
    ("hr",            "Отдел кадров",                         "Начальник отдела кадров", None),
    ("meta_comp",     "Центр развития метакомпетенций",       "Директор центра", None),
    ("digital_learn", "Управление цифрового обучения",        "Начальник управления", None),
    ("nir_lab",       "Научно-исследовательская лаборатория", "Руководитель лаборатории", None),
    ("npp_center",    "Центр подготовки научно-педагогических работников", "Директор центра", None),
    ("educ_work",     "Управление воспитательной работы и социализации", "Начальник управления", None),
    ("career",        "Центр карьеры",                        "Директор центра карьеры", None),
    ("student_hub",   "Студенческий цифровой хаб",            "Куратор хаба", None),
    ("it_support",    "Отдел информационно-технического обеспечения", "Начальник отдела", None),
    ("digital_serv",  "Лаборатория разработки цифровых сервисов", "Руководитель лаборатории", None),
    ("marketing",     "Центр маркетинга и развития",          "Директор по маркетингу и инновациям", None),
    ("advertising",   "Отдел рекламы",                        "Начальник отдела рекламы", None),
    ("general",       "Общий отдел",                          "Начальник отдела", None),
    ("org_control",   "Организационно-контрольное управление", "Начальник управления", None),
    ("other",         "Другое",                               "Уточняется при загрузке", None),
]

# Как часто подразделение обещает обновлять данные. Подсказка в анкете:
# отдельные подразделения сами выбирают при загрузке, здесь — valeurs по
# умолчанию для расчёта next_expected.
FREQ_DAYS = {
    "разово": None,
    "ежемесячно": 30,
    "ежеквартально": 90,
    "раз в семестр": 180,
    "ежегодно": 365,
}

LASTNAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Смирнов",
             "Волков", "Лебедев", "Козлов", "Новиков", "Морозов"]
FIRSTNAMES = ["Алексей", "Мария", "Дмитрий", "Анна", "Иван",
              "Елена", "Никита", "Ольга", "Сергей", "Наталья"]
REASONS = ["академическая задолженность", "неуспеваемость",
           "семейные обстоятельства", "перевод в другой вуз",
           "по собственному желанию", "непрохождение аттестации"]

# base / vol — параметры генератора для ДЕМО-метрик.
# Официальных метрик здесь нет: у них одна точка, а не ряд.
GEN = {
    "plan-enroll": (96, 8),
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


# Единицы, для которых разбивка по кафедрам осмысленна: это счётные
# величины. Для процентов, баллов, индексов и часов разбивка бессмысленна:
# кафедра получает долю от процента, и «успеваемость 11,4 %» выглядит
# как провал, хотя на деле это доля университетских 94 % (0,82 · 11,4).
COUNT_UNITS = {"чел.", "шт.", "шт./год", "тыс", "млн ₽"}


def compute_status(value, direction, target):
    # Целевого порога в документах нет — статус по нему считать не из чего.
    # Показывать «красный» по несуществующей цели было бы выдумкой.
    if target is None:
        return "none"
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
    """Полный срез витрины — та же логика, что build() в data.js.

    У метрик две разные природы, и различать их приходится прямо здесь.

    * Официальные — значение взято из документа университета. Ряда у
      него нет: отчёт даёт одну точку на 01.04.2026, план ФХД — одну на
      2026 год. Поэтому series состоит из одного значения, разбивки по
      кафедрам нет (её нет и в документах), prev и delta равны None.
      Придумывать к такому числу одиннадцать прошлых месяцев нельзя:
      ровный график ректор прочтёт как историю.
    * Демонстрационные — полный ряд, разбивка по кафедрам, drill-down.
      Это единственные числа, которых в документах не существует.
    """
    rnd = mulberry32(seed)
    year = 2026
    periods = [(f"{year}-{m:02d}", m) for m in range(1, 13)]

    metrics = []
    for i, (mid, block, title, unit, direction, target) in enumerate(METRICS):
        official = mid in OFFICIAL

        if official:
            current, src_note = OFFICIAL[mid]
            series = [current]
            prev = delta = None
            dept_values, drill = [], {}
        else:
            series = make_series(mid, seed + i * 101)
            current = series[-1]
            prev = series[-2] if len(series) > 1 else series[0]
            delta = ((current - prev) / prev * 100) if prev else 0.0
            src_note = None

            # Доли подразделений обязаны в сумме давать итог по университету.
            # Раньше каждое подразделение получало base * (0.82...1.18)
            # независимо, и девять таких значений давали примерно девятикратный
            # итог. Дефект был не виден, пока цифры показывались только общей
            # строкой; как только агенты стали считать разбивку по кафедрам,
            # части перестали сходиться с целым.
            #
            # Разбивка строится только для счётных величин. Для процентов
            # она бессмысленна: кафедра получила бы долю от процента, и
            # «успеваемость 11,4 %» читалось бы как провал там, где
            # на деле 94 % по университету.
            if unit in COUNT_UNITS:
                weights = [0.82 + rnd() * 0.36 for _ in DEPTS]
                wsum = sum(weights)
                dept_values = []
                for di, org in enumerate(DEPTS):
                    dept_id, dept_name = org[0], org[1]
                    v = current * (weights[di] / wsum)
                    rounded = js_round(v * 10) / 10
                    dept_values.append({
                        "dept_id": dept_id, "dept_name": dept_name,
                        "value": rounded,
                        "share": (rounded / current * 100) if current else 0.0,
                    })
            else:
                dept_values = []

            drill = {}
            if dept_values:
                for di, org in enumerate(DEPTS):
                    drill[org[0]] = make_drill(mid, seed + i * 7 + di, org[1],
                                               org[0], direction)

        metrics.append({
            "metric_id": mid, "block_id": block, "title": title,
            "unit": unit, "direction": direction, "target": target,
            "source": "official" if official else "demo",
            "src_note": src_note,
            "has_history": not official,
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
    else:
        # Без пересборки в справочниках остаются записи прежней
        # структуры: академия, которой больше нет, продолжала бы
        # числиться в сводке. Убираем всё, чего нет в определениях.
        keep_orgs = {o[0] for o in ORGS}
        for row in conn.execute("SELECT org_id FROM dim_org").fetchall():
            if row["org_id"] not in keep_orgs:
                conn.execute("DELETE FROM fact_metric WHERE org_id = ?",
                             (row["org_id"],))
                conn.execute("DELETE FROM fact_detail WHERE org_id = ?",
                             (row["org_id"],))
                conn.execute("DELETE FROM dim_org WHERE org_id = ?",
                             (row["org_id"],))
        keep_mets = {m[0] for m in METRICS}
        for row in conn.execute("SELECT metric_id FROM dim_metric").fetchall():
            if row["metric_id"] not in keep_mets:
                conn.execute("DELETE FROM fact_metric WHERE metric_id = ?",
                             (row["metric_id"],))
                conn.execute("DELETE FROM fact_detail WHERE metric_id = ?",
                             (row["metric_id"],))
                conn.execute("DELETE FROM metric_alias WHERE metric_id = ?",
                             (row["metric_id"],))
                conn.execute("DELETE FROM dim_metric WHERE metric_id = ?",
                             (row["metric_id"],))

    for i, (pid, mnum) in enumerate(data["periods"]):
        conn.execute(
            "INSERT OR REPLACE INTO dim_period(period_id, ord, label, year, month)"
            " VALUES(?,?,?,?,?)",
            (pid, i, MONTHS[mnum - 1], int(pid[:4]), mnum))
    for oid, title, parent, order, kind in ORGS:
        conn.execute(
            "INSERT OR REPLACE INTO dim_org(org_id, title, parent_id, kind, ord)"
            " VALUES(?,?,?,?,?)", (oid, title, parent, kind, order))
    for i, (mid, block, title, unit, direction, target) in enumerate(METRICS):
        src = OFFICIAL.get(mid)
        conn.execute(
            "INSERT OR REPLACE INTO dim_metric"
            "(metric_id, block_id, title, unit, dir, target, source, src_note, ord)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (mid, block, title, unit, direction, target,
             "official" if src else "demo", src[1] if src else None, i))
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
        # У официальной метрики одна точка — кладём её в последний
        # период и не выдумываем предыдущие одиннадцать.
        periods_to_write = ([last_period] if not m["has_history"]
                            else [p[0] for p in data["periods"]])
        for pi, pid in enumerate(periods_to_write):
            value = m["series"][-1] if not m["has_history"] else m["series"][pi]
            conn.execute(
                "INSERT OR REPLACE INTO fact_metric"
                "(period_id, metric_id, org_id, value, status) VALUES(?,?,?,?,?)",
                (pid, m["metric_id"], "ALL", value,
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


def seed_divisions(conn):
    """Заводит подразделения с логином и паролем по умолчанию.

    Пароль — тот же, что логин (честно, как в старой системе). Каждое
    подразделение заходит под своим логином, видит и правит только свои
    загрузки. Админ — отдельный логин `admin`, права на всё.
    """
    import hashlib
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    admin_pass = hashlib.pbkdf2_hmac("sha256", b"admin", b"dashboard-admin", 100_000).hex()
    conn.execute(
        "INSERT OR REPLACE INTO division(division_id, title, login, pass_hash, contact, created_at)"
        " VALUES(?,?,?,?,?,?)",
        ("admin", "Администратор", "admin", admin_pass, "rector@iubip.ru", now))
    for did, title, responsible, contact in DIVISIONS:
        login = did
        pass_hash = hashlib.pbkdf2_hmac("sha256", login.encode(), f"dashboard-{did}".encode(), 100_000).hex()
        conn.execute(
            "INSERT OR REPLACE INTO division(division_id, title, login, pass_hash, contact, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (did, title, login, pass_hash, responsible, now))
    conn.commit()


def next_expected_for(frequency: str, from_date=None) -> str | None:
    """Дата следующей ожидаемой загрузки по частоте обновления."""
    if frequency not in FREQ_DAYS or FREQ_DAYS[frequency] is None:
        return None
    import datetime
    base = from_date or datetime.date.today()
    days = FREQ_DAYS[frequency]
    return (base + datetime.timedelta(days=days)).isoformat()


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
                 "ооп", "фгос", "самообследовани", "выпуск", "приемная кампания"],
    "results": ["attestaci", "аттестац", "rezultat", "результатив", "успеваем",
                "zadolzhen", "задолжен", "качество знаний", "учебн", "егэ", "сга",
                "трудоустро"],
    "finance": ["finans", "финанс", "byudzhet", "бюджет", "economic",
                "эконом", "выручка", "доход", "расход", "фот",
                "оплаты труда", "аренда", "план фхд", "финансово-хозяйственн"],
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
             "name": r["title"], "title": r["title"],
             "parentId": r["parent_id"], "kind": r["kind"]}
            for r in conn.execute(
                "SELECT org_id, title, parent_id, kind FROM dim_org ORDER BY ord")]
    # Фильтр подразделений показывает кафедры: разбивка показателей
    # идёт по ним, а академии — верхний уровень сводки.
    depts = [o for o in orgs if o["kind"] == "dept"]
    academies = [o for o in orgs if o["kind"] == "academy"]
    blocks = [{"id": b[0], "title": b[1], "icon": b[2], "role": b[3], "desc": b[4]}
              for b in BLOCKS]

    labels = [p["label"] for p in periods]

    # Провенанс показателя: каким подразделением, когда и на основании
    # какого документа подтверждено значение. Пустой список = цифра
    # пока ничем не подписана (демо или официальный документ без
    # загрузки через анкету).
    prov_by_metric = {}
    for r in conn.execute(
            "SELECT ms.metric_id, ms.confirmed, dm.title, dm.period,"
            " dm.uploaded_at, dm.next_expected, dm.responsible,"
            " d.title AS div_title"
            " FROM metric_source ms"
            " JOIN doc_meta dm ON dm.doc_id = ms.doc_id"
            " JOIN division d ON d.division_id = dm.division_id"
            " ORDER BY dm.uploaded_at DESC"):
        prov_by_metric.setdefault(r["metric_id"], []).append({
            "division": r["div_title"], "title": r["title"],
            "period": r["period"], "uploaded_at": r["uploaded_at"],
            "next_expected": r["next_expected"],
            "responsible": r["responsible"],
            "confirmed": bool(r["confirmed"])})

    metrics = []
    for row in conn.execute(
            "SELECT * FROM dim_metric ORDER BY ord"):
        mid = row["metric_id"]
        official = row["source"] == "official"
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

        # У официальной метрики ряд состоит из одной точки: сравнивать
        # не с чем, и prev/delta остаются пустыми. Интерфейс по
        # has_history покажет «исторического ряда нет» вместо графика.
        if official or len(series) < 2:
            prev = delta = None
        else:
            prev = series[-2]
            delta = ((current - prev) / prev * 100) if prev else 0.0
        metrics.append({
            "id": mid, "block": row["block_id"], "title": row["title"],
            "unit": row["unit"], "dir": row["dir"], "target": row["target"],
            "source": row["source"], "srcNote": row["src_note"],
            "hasHistory": not official,
            "prov": prov_by_metric.get(mid, []),
            "series": series, "current": current, "prev": prev,
            "delta": delta,
            "status": compute_status(current, row["dir"], row["target"]),
            "dept": dept, "drill": drill,
        })

    standards = [{"code": r["code"], "level": r["level"], "title": r["title"],
                  "approved": r["approved"], "updated": r["updated"]}
                 for r in conn.execute(
                     "SELECT code, level, title, approved, updated"
                     " FROM dim_standard ORDER BY code")]

    return {"blocks": blocks, "metrics": metrics,
            "dept": depts, "academies": academies,
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