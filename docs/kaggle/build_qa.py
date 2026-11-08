# -*- coding: utf-8 -*-
"""Собирает вопросы для замера и датасет для QLoRA из одной базы знаний.

Почему один сборщик на два дела. Вопросы, на которых меряем, и вопросы,
на которых учим, обязаны быть устроены одинаково: иначе замер показывает
одно, а обучение учит другому. Здесь они строятся из одних и тех же
фактов и одного разбиения.

Три правила, без которых обучение бессмысленно:

1. Разбиение по ДОКУМЕНТАМ, а не по строкам. Карточка вуза, попавшая
   в train, не встречается в test. Иначе метрика показывает заученное.
2. Формат ответа — боевой: системный промпт `prompts.SYSTEM`, контекст
   через `llm.build_context`, ссылка `[1]` и 3–6 предложений. Адаптер,
   обученный другому формату, в бою будет спорить с промптом.
3. Каждый факт проверяется: число из ответа обязано быть во фрагменте.
   Для разностей проверяются оба слагаемых — сама разность в источнике
   отсутствует по определению.

    python docs/kaggle/build_qa.py
    -> docs/eval/questions.jsonl
    -> docs/kaggle/train.jsonl, dev.jsonl, test.jsonl
    -> docs/kaggle/dataset-report.txt
"""
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend import llm, prompts  # noqa: E402

KB = ROOT / "knowledge" / "official" / "monitoring"
OUT_EVAL = ROOT / "docs" / "eval"
OUT_KAGGLE = ROOT / "docs" / "kaggle"
STORE = ROOT / "data" / "monitoring" / "structured" / "observations.jsonl"

# Явное разбиение ключевых документов. Региональные таблицы — это
# проверка обобщения: 2025 не должна встречаться при обучении.
FIXED_SPLIT = {
    "2024-iubip.md": "train",
    "2025-iubip.md": "train",
    "summary.md": "train",
    "spo/2025-iubip.md": "train",
    "region-2024.md": "dev",
    "region-2025.md": "test",
    "region-aggregates-2024.md": "dev",
    "region-aggregates-2025.md": "test",
}

DETAIL_RE = re.compile(r"^- (.+?)(?: \((.+?)\))?: \*\*(.+?)\*\*$")
CARD_RE = re.compile(r"^- (.+?)(?: \((.+?)\))?: (.+)$")
SECTION_RE = re.compile(r"^## (.+)$")
ROW_RE = re.compile(r"^(\d+)\.\s+(.+?)\s+—\s+(-?[\d.,]+)(?:\s+←.*)?$")

NO_ANSWER = ("В загруженных документах ответ на этот вопрос не найден. "
             "Возможно, нужный документ ещё не добавлен.")

rows = []          # вопросы для замера
examples = []      # то же в формате chat
dropped = []
counts = {}


def norm_num(text: str) -> str:
    """Число к сравнимому виду: 1 350 / 1350, 60.74 / 60,74."""
    s = str(text or "").replace("\u00a0", " ").replace(" ", "")
    return s.replace(",", ".").strip()


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).lower()


def split_for(rel_path: str) -> str:
    # rel_path приходит как official/monitoring/..., а таблица задана
    # короткими именами: сравниваем по хвосту, иначе весь регион
    # попадает в train и проверять обобщение не на чем.
    short = rel_path.split("official/monitoring/", 1)[-1]
    if short in FIXED_SPLIT:
        return FIXED_SPLIT[short]
    if rel_path.startswith("by-vuz/"):
        stem = pathlib.Path(rel_path).stem
        inst = stem.split("-")[0]
        bucket = int(hashlib.sha1(inst.encode("utf-8")).hexdigest()[:8], 16) % 100
        return "train" if bucket < 80 else ("dev" if bucket < 90 else "test")
    return "train"


def chunk(doc_title, heading, text):
    return {"doc_title": doc_title, "heading": heading, "text": text,
            "is_demo": False}


def context_text(chunks):
    return llm.build_context(chunks)


def held_out(key, total, count):
    bucket = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    return bucket % total < count


def add(task, question, answer, chunks, expected_paths,
        expected_value=None, operands=None, source="", refusal_kind=None,
        split=None):
    """Добавляет вопрос после проверки, что опора есть во фрагменте.

    `refusal_kind` отделяет два разных отказа, и это важно для замера:
    «данных нет вообще» (Scopus, эндаумент) и «в этом фрагменте нет
    нужного года». Второй случай витрина закрывает честно — число за
    другой выпуск в источнике есть, поэтому считать его перехватом нельзя.

    `split` перекрывает разбиение по документу. Нужен для отказов:
    вопрос «данных нет» не опирается на содержание документа, поэтому
    его можно держать в test, не открывая ответ.
    """
    joined = norm_text(" ".join(c["text"] for c in chunks))
    if expected_value is not None and norm_num(expected_value) not in norm_num(joined):
        dropped.append({"task": task, "question": question,
                        "missing": expected_value})
        return
    for op in operands or []:
        if norm_num(op) not in norm_num(joined):
            dropped.append({"task": task, "question": question, "missing": op})
            return

    rel = expected_paths[0]
    split = split or split_for(rel)
    qid = f"q{len(rows) + 1:04d}"
    messages = [
        {"role": "system", "content": prompts.SYSTEM},
        {"role": "user", "content": prompts.USER_TMPL.format(
            context=context_text(chunks), question=question)},
        {"role": "assistant", "content": answer},
    ]
    rows.append({
        "id": qid,
        "split": split,
        "task": task,
        "question": question,
        "answer": answer,
        "expected_paths": expected_paths,
        "expected_value": expected_value,
        "operands": operands or [],
        "source": source,
        "refusal_kind": refusal_kind,
        "context": chunks,
        "messages": messages,
    })
    counts[(split, task)] = counts.get((split, task), 0) + 1

    examples.append({
        "id": qid,
        "split": split,
        "task": task,
        "messages": messages,
    })


def detail_facts(year):
    path = KB / f"{year}-iubip.md"
    title = f"ИУБиП в мониторинге эффективности, выпуск {year}"
    facts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = DETAIL_RE.match(line.strip())
        if not m:
            continue
        facts.append({"title": m.group(1), "unit": m.group(2) or "",
                      "value": m.group(3), "line": line.strip().lstrip("- ")})
    return title, facts


# ---------- 1. ИУБиП: извлечение и динамика ------------------------------
detail = {}
for year in ("2024", "2025"):
    title, facts = detail_facts(year)
    detail[year] = {f["title"]: f for f in facts}
    for f in facts:
        unit = f" {f['unit']}" if f["unit"] else ""
        add(
            "extraction",
            f"Какое значение показателя «{f['title']}» у ИУБиП "
            f"в выпуске {year}?",
            f"В выпуске {year} значение показателя «{f['title']}» "
            f"составляет {f['value']}{unit} [1].",
            [chunk(title, "", f["line"])],
            [f"official/monitoring/{year}-iubip.md"],
            expected_value=f["value"],
            source=f"мониторинг эффективности, выпуск {year}, карточка ИУБиП",
        )

# Сравнение двух выпусков: оба числа в контексте, ответ — разность.
for common in sorted(set(detail["2024"]) & set(detail["2025"])):
    a, b = detail["2024"][common], detail["2025"][common]
    if not (re.fullmatch(r"-?[\d\s.,]+", a["value"])
            and re.fullmatch(r"-?[\d\s.,]+", b["value"])):
        continue  # «да/нет» — не число, разность не считается
    na, nb = float(norm_num(a["value"])), float(norm_num(b["value"]))
    diff = round(nb - na, 2)
    if diff == 0:
        continue
    unit = f" {a['unit']}" if a["unit"] else ""
    add(
        "comparison",
        f"На сколько изменилось значение показателя «{common}» у ИУБиП "
        f"с выпуска 2024 по выпуск 2025?",
        f"Показатель «{common}» изменился на {abs(diff):.2f}{unit}: "
        f"в выпуске 2024 — {a['value']}{unit}, в выпуске 2025 — "
        f"{b['value']}{unit} [1,2].",
        [chunk("ИУБиП, выпуск 2024", "", a["line"]),
         chunk("ИУБиП, выпуск 2025", "", b["line"])],
        ["official/monitoring/2024-iubip.md",
         "official/monitoring/2025-iubip.md"],
        operands=[a["value"], b["value"]],
        source="мониторинг эффективности, выпуски 2024 и 2025, карточка ИУБиП",
    )

# ---------- 2. Региональные таблицы --------------------------------------
REGION_UNITS = {}
for year in ("2024", "2025"):
    mon = json.loads((ROOT / "data" / "monitoring.json").read_text(encoding="utf-8"))
    REGION_UNITS[year] = {k: v.get("unit", "")
                          for k, v in mon["years"][year]["indicators"].items()}

for year in ("2024", "2025"):
    path = KB / f"region-{year}.md"
    title = f"Вузы Ростовской области в мониторинге, выпуск {year}"
    rel = f"official/monitoring/region-{year}.md"
    section, section_rows = None, []

    def flush():
        if not section or not section_rows:
            return
        m_code = re.match(r"(E\.?\d+)", section)
        code = m_code.group(1).replace(".", "") if m_code else ""
        unit = REGION_UNITS[year].get(code, "")
        unit_text = f" {unit}" if unit else ""
        lines = [r["line"] for r in section_rows]
        iubip = next((r for r in section_rows if r["is_iubip"]), None)
        for r in section_rows:
            add(
                "extraction",
                f"Какое значение показателя «{section}» у вуза "
                f"«{r['name']}» в выпуске {year}?",
                f"У вуза «{r['name']}» значение показателя «{section}» "
                f"в выпуске {year} — {r['value']}{unit_text} [1].",
                [chunk(title, section, r["line"])],
                [rel],
                expected_value=r["value"],
                source=f"мониторинг, выпуск {year}, вузы Ростовской области",
            )
        # Сравнение с ИУБиП: значимая для ректора задача, оба числа рядом.
        if iubip:
            for r in [x for x in section_rows if not x["is_iubip"]][:3]:
                diff = round(float(iubip["value"].replace(",", "."))
                             - float(r["value"].replace(",", ".")), 2)
                add(
                    "comparison",
                    f"На сколько значение показателя «{section}» у ИУБиП "
                    f"отличается от значения у вуза «{r['name']}» "
                    f"в выпуске {year}?",
                    f"Разница — {diff:.2f}{unit_text}: у ИУБиП "
                    f"{iubip['value']}{unit_text}, у вуза «{r['name']}» — "
                    f"{r['value']}{unit_text} [1].",
                    [chunk(title, section, iubip["line"]),
                     chunk(title, section, r["line"])],
                    [rel],
                    operands=[iubip["value"], r["value"]],
                    source=f"мониторинг, выпуск {year}, вузы Ростовской области",
                )
        del lines

    for line in path.read_text(encoding="utf-8").splitlines():
        sec = SECTION_RE.match(line.strip())
        if sec:
            flush()
            section, section_rows = sec.group(1), []
            continue
        row = ROW_RE.match(line.strip())
        if row and section:
            section_rows.append({
                "place": row.group(1),
                "name": row.group(2),
                "value": row.group(3),
                "is_iubip": "← ИУБиП" in line,
                "line": line.strip(),
            })
    flush()

# ---------- 3. Карточки других вузов -------------------------------------
for card in sorted((KB / "by-vuz").glob("*.md")):
    rel = f"official/monitoring/by-vuz/{card.name}"
    if split_for(rel) != "train":
        continue
    text = card.read_text(encoding="utf-8")
    title = next((l.lstrip("# ").strip() for l in text.splitlines()
                  if l.startswith("# ")), card.stem)
    year = "2025" if card.stem.endswith("-2025") else "2024"
    taken = 0
    for line in text.splitlines():
        m = CARD_RE.match(line.strip())
        if not m or taken >= 6:
            continue
        fact_title, unit, value = m.group(1), m.group(2) or "", m.group(3)
        if len(fact_title) < 12 or len(re.findall(r"[А-Яа-яЁё]", fact_title)) < 5:
            continue
        if not re.search(r"\d", value):
            continue
        unit_text = f" {unit}" if unit else ""
        add(
            "extraction",
            f"Какое значение показателя «{fact_title}» у вуза "
            f"«{title}» в выпуске {year}?",
            f"У вуза «{title}» показатель «{fact_title}» в выпуске {year} "
            f"составляет {value}{unit_text} [1].",
            [chunk(title, "", line.strip().lstrip("- "))],
            [rel],
            expected_value=value.split(";")[0].strip(),
            source=f"мониторинг, выпуск {year}, карточка вуза",
        )
        taken += 1

# ---------- 4. Отказы ----------------------------------------------------
# Первый прогон показал: 5% отказов в train недостаточно — адаптер
# выучивает «всегда отвечать числом» и игнорирует правило 3 промпта.
# Цель: ~25% отказов в train, с акцентом на главный провал — контекст
# содержит число за другой год, вопрос спрашивает не тот год.

_, facts_2025 = detail_facts("2025")
refusal_context = [chunk("ИУБиП, выпуск 2025", "",
                         f["line"]) for f in facts_2025[:6]]
absent_questions = [
    "Сколько публикаций в Scopus у ИУБиП в 2025 году?",
    "Каков объём эндаумент-фонда ИУБиП?",
    "Сколько студентов учится в ИУБиП в 2027 году?",
    "Какое место занял ИУБиП в рейтинге QS в 2025 году?",
    "Сколько иностранных студентов из Китая обучается в ИУБиП?",
    "Каков средний балл ЕГЭ у ИУБиП в 2026 году?",
    "Сколько публикаций в Web of Science у ИУБиП в 2025 году?",
    "Какой индекс Хирша у ИУБиП в 2025 году?",
    "Сколько мест в общежитии у ИУБиП?",
    "Какое место занял ИУБиП в рейтинге THE в 2025 году?",
    "Каков бюджет ИУБиП на 2026 год?",
    "Сколько аспирантов защитило диссертации в ИУБиП в 2025 году?",
    "Сколько договоров о целевом обучении заключил ИУБиП в 2025 году?",
    "Какова средняя зарплата выпускников ИУБиП через год после выпуска?",
    "Сколько студентов из Индии обучается в ИУБиП?",
    "Каков проходной балл на юриспруденцию в ИУБиП в 2026 году?",
    "Сколько студентов учится в ИУБиП в 2028 году?",
    "Какое значение показателя трудоустройства выпускников ИУБиП в 2027 году?",
]
for i, question in enumerate(absent_questions):
    add("refusal", question, NO_ANSWER, refusal_context,
        ["official/monitoring/2025-iubip.md"], refusal_kind="absent_data",
        split=("train", "dev", "test")[i % 3])

# Отказ по году: фрагмент реальный, но год в нём другой.
title_2024, facts_2024 = detail_facts("2024")
for f in facts_2024[:20]:
    add(
        "refusal",
        f"Какое значение показателя «{f['title']}» у ИУБиП в 2025 году?",
        NO_ANSWER,
        [chunk("ИУБиП, выпуск 2024", "", f["line"])],
        ["official/monitoring/2024-iubip.md"],
        refusal_kind="year_mismatch",
    )
# Зеркально: контекст 2025, вопрос про 2024.
for f in facts_2025[:20]:
    add(
        "refusal",
        f"Какое значение показателя «{f['title']}» у ИУБиП в 2024 году?",
        NO_ANSWER,
        [chunk("ИУБиП, выпуск 2025", "", f["line"])],
        ["official/monitoring/2025-iubip.md"],
        refusal_kind="year_mismatch",
    )
# Будущий/прошлый год при наличии реального контекста — именно это
# сломало адаптер в замере (вопрос про 2027 → число за 2025).
for f in facts_2025[:18]:
    for year in ("2026", "2027"):
        add(
            "refusal",
            f"Какое значение показателя «{f['title']}» у ИУБиП в {year} году?",
            NO_ANSWER,
            [chunk("ИУБиП, выпуск 2025", "", f["line"])],
            ["official/monitoring/2025-iubip.md"],
            refusal_kind="year_mismatch",
            split="train" if year == "2026" else ("train", "test")[
                held_out("ref-future:" + f["title"], 10, 2)],
        )
for f in facts_2024[:12]:
    add(
        "refusal",
        f"Какое значение показателя «{f['title']}» у ИУБиП в 2026 году?",
        NO_ANSWER,
        [chunk("ИУБиП, выпуск 2024", "", f["line"])],
        ["official/monitoring/2024-iubip.md"],
        refusal_kind="year_mismatch",
    )


def region_rows(year, limit):
    """Строки региональной таблицы без ИУБиП — для отказов в dev/test."""
    title = f"Вузы Ростовской области в мониторинге, выпуск {year}"
    section, out = None, []
    for line in (KB / f"region-{year}.md").read_text(encoding="utf-8").splitlines():
        sec = SECTION_RE.match(line.strip())
        if sec:
            section = sec.group(1)
            continue
        row = ROW_RE.match(line.strip())
        if row and section and "← ИУБиП" not in line:
            out.append({"section": section, "name": row.group(2),
                        "line": line.strip()})
    return title, out[:limit]


# test: контекст 2025, вопрос про 2024/2026 — модель обязана отказать.
title_25, rows_25 = region_rows("2025", 12)
for r in rows_25[:5]:
    add("refusal",
        f"Какое значение показателя «{r['section']}» у вуза «{r['name']}» "
        f"в выпуске 2024?",
        NO_ANSWER,
        [chunk(title_25, r["section"], r["line"])],
        ["official/monitoring/region-2025.md"],
        refusal_kind="year_mismatch")
for r in rows_25[5:12]:
    add("refusal",
        f"Какое значение показателя «{r['section']}» у вуза «{r['name']}» "
        f"в выпуске 2026?",
        NO_ANSWER,
        [chunk(title_25, r["section"], r["line"])],
        ["official/monitoring/region-2025.md"],
        refusal_kind="year_mismatch",
        split="test" if held_out("ref-reg26:" + r["name"], 10, 3) else "train")

# dev: контекст 2024, вопрос про 2025.
title_24, rows_24 = region_rows("2024", 5)
for r in rows_24:
    add("refusal",
        f"Какое значение показателя «{r['section']}» у вуза «{r['name']}» "
        f"в выпуске 2025?",
        NO_ANSWER,
        [chunk(title_24, r["section"], r["line"])],
        ["official/monitoring/region-2024.md"],
        refusal_kind="year_mismatch")

# Чужой вуз в контексте, вопрос про «нас/ИУБиП» — нельзя подставлять
# чужое число (правило 5.1 промпта).
title_25b, rows_25b = region_rows("2025", 40)
for r in rows_25b[12:32]:
    add("refusal",
        f"Какое значение показателя «{r['section']}» у ИУБиП в выпуске 2025?",
        NO_ANSWER,
        [chunk(title_25b, r["section"], r["line"])],
        ["official/monitoring/region-2025.md"],
        refusal_kind="wrong_org",
        split="train" if not held_out("ref-org:" + r["name"] + r["section"], 10, 2)
        else "test")

# Показатель не в фрагменте: контекст про один E, вопрос про другой.
e_pairs = [
    ("E.1. Образовательная деятельность", "E.8. Дополнительный показатель"),
    ("E.2. Научно-исследовательская деятельность", "E.1. Образовательная деятельность"),
    ("E.3. Международная деятельность", "E.4. Финансово-экономическая деятельность"),
    ("E.4. Финансово-экономическая деятельность", "E.2. Научно-исследовательская деятельность"),
    ("E.8. Дополнительный показатель", "E.3. Международная деятельность"),
]
for i, (ctx_section, asked) in enumerate(e_pairs):
    row = next((r for r in rows_25b if r["section"] == ctx_section), None)
    if not row:
        continue
    add("refusal",
        f"Какое значение показателя «{asked}» у вуза «{row['name']}» "
        f"в выпуске 2025?",
        NO_ANSWER,
        [chunk(title_25b, row["section"], row["line"])],
        ["official/monitoring/region-2025.md"],
        refusal_kind="missing_indicator",
        split=("train", "dev", "test")[i % 3])

# ---------- 5. Структура базы -------------------------------------------
summary = (KB / "summary.md").read_text(encoding="utf-8")
head = [l for l in summary.splitlines()[:8] if l.strip()]
add("structure", "Какие выпуски мониторинга есть в базе?",
    "В базе два выпуска мониторинга — 2024 и 2025. В таблицах источника "
    "значения подписаны годом выпуска: в выпуске 2025 значения за "
    "2025 год [1].",
    [chunk("Сводка по мониторингу", "", "\n".join(head))],
    ["official/monitoring/summary.md"])
add("structure", "Как читать место вуза в Ростовской области?",
    "Место — среди вузов Ростовской области, а не среди всех вузов РФ. "
    "Перцентиль 100 — лучший результат в области, 50 — медиана [1].",
    [chunk("Сводка по мониторингу", "",
           "Место — среди вузов Ростовской области, а не среди всех вузов РФ.\n"
           "Перцентиль 100 — лучший результат в области, 50 — медиана.")],
    ["official/monitoring/summary.md"])

# ---------- 6. Структурированная копия: направления, карточка, СПО --------
# Эти пары строятся из data/monitoring/structured/observations.jsonl, а не
# из markdown. Так обучение видит те же числа, что и точный lookup.
# Восемь показателей направлений и четыре показателя СПО держим в test:
# документ тот же, но конкретную строку модель не видела.

def store_rows():
    if not STORE.exists():
        raise SystemExit("нет structured/observations.jsonl; сначала: "
                         "python -m backend.monitoring_store build")
    return [json.loads(line) for line in
            STORE.read_text(encoding="utf-8").splitlines() if line.strip()]


def held_out(key, total, count):
    bucket = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    return bucket % total < count


def as_float(text):
    try:
        return float(norm_num(text))
    except (TypeError, ValueError):
        return None


def fmt(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text.replace(".", ",")
    return str(value)


def end(unit):
    """Точка в конце предложения, но не вторая после «чел.»."""
    return "" if str(unit or "").endswith(".") else "."


METRIC_TITLES = {
    "contingent": "контингент",
    "share_org": "доля в организации",
    "share_region": "доля в регионе",
    "vuz_count": "число вузов в регионе",
    "branch_count": "число филиалов в регионе",
    "subject_share": "доля в субъекте РФ",
    "city": "число организаций в городе",
    "region": "число организаций в регионе",
    "rf": "число организаций в РФ",
}

store_facts = store_rows()

# 6.1. 57 показателей направлений: извлечение и сравнение с медианами.
for fact in store_facts:
    if not (fact["level"] == "VO" and fact["release"] == 2025
            and fact.get("section") == "III" and fact.get("org_id") == "1971"
            and fact.get("kind") == "value"):
        continue
    code, title = fact["code"], fact["title"]
    value, unit = fact.get("value_text"), fact.get("unit") or ""
    med_private = fact.get("median_private")
    med_region = fact.get("median_region")
    extra = fact.get("extra") or {}
    split = "test" if held_out("dir:" + code, 57, 8) else "train"
    text = f"{code}. {title} — {value} {unit}".strip()
    if med_private is not None:
        text += f"; медиана частных вузов — {fmt(med_private)} {unit}"
    if med_region is not None:
        text += f"; медиана региона — {fmt(med_region)} {unit}"
    answer = (f"В выпуске 2025 значение показателя «{title}» "
              f"у ИУБиП — {value} {unit} [1].")
    if med_private is not None:
        answer += (f" Медиана частных вузов — {fmt(med_private)} "
                   f"{unit}{end(unit)}")
    if med_region is not None:
        answer += (f" Медиана региона — {fmt(med_region)} "
                   f"{unit}{end(unit)}")
    add("extraction",
        f"Какое значение показателя «{title}» у ИУБиП в выпуске 2025?",
        answer,
        [chunk("ИУБиП, выпуск 2025: направления деятельности",
               fact.get("block") or "", text)],
        ["official/monitoring/2025-directions.md"],
        expected_value=value,
        source="мониторинг, раздел III, выпуск 2025",
        split=split)
    if (med_private is not None and extra.get("comparable_private")
            and not extra.get("capped_private")):
        number, median = as_float(value), as_float(med_private)
        if number is not None and median is not None and abs(number - median) > 1e-9:
            diff = number - median
            add("comparison",
                f"На сколько значение показателя «{title}» у ИУБиП "
                f"отличается от медианы частных вузов в выпуске 2025?",
                f"Разница — {fmt(diff)} {unit}: у ИУБиП {value} {unit}, "
                f"медиана частных вузов {fmt(med_private)} {unit} [1].",
                [chunk("ИУБиП, выпуск 2025: направления деятельности",
                       fact.get("block") or "", text)],
                ["official/monitoring/2025-directions.md"],
                operands=[value, fmt(med_private)],
                source="мониторинг, раздел III, выпуск 2025",
                split=split)

# 6.2. Разделы I и IV: сведения, контингент, отрасли, УГН(С).
card_facts = [fact for fact in store_facts
              if fact["level"] == "VO" and fact["release"] == 2025
              and fact.get("org_id") == "1971"
              and fact.get("section") in ("I", "IV")
              and fact.get("kind") in ("profile", "count", "share")]
for fact in card_facts:
    code, title = fact["code"], fact["title"]
    value, unit = fact.get("value_text"), fact.get("unit") or ""
    metric = (fact.get("extra") or {}).get("metric")
    split = "train"
    if code.startswith("info."):
        if held_out("card:" + code, 10, 1):
            split = "test"
        if held_out("card-dev:" + code, 10, 1):
            split = "dev"
        question = f"Что известно о «{title}» ИУБиП в карточке мониторинга?"
        answer = f"В карточке мониторинга «{title}»: {value} [1]."
        text = f"{title}: {value}"
    elif code.startswith("branch."):
        metric_title = METRIC_TITLES.get(metric, metric or "показатель")
        question = (f"Каков {metric_title} по отрасли «{title}» у ИУБиП "
                    f"в выпуске 2025?")
        answer = (f"{metric_title.capitalize()} по отрасли «{title}» — "
                  f"{value} {unit} [1].")
        text = f"Отрасль «{title}»: {metric_title} — {value} {unit}"
    elif (code.startswith("ugn.")
          or (fact.get("block") == "Реализуемые УГН(С)" and metric)):
        metric_title = METRIC_TITLES.get(metric, metric or "показатель")
        question = (f"Каков {metric_title} по УГН(С) {code} «{title}» "
                    f"у ИУБиП в выпуске 2025?")
        answer = (f"По УГН(С) {code} «{title}» {metric_title} — "
                  f"{value} {unit} [1].")
        text = f"УГН(С) {code} «{title}»: {metric_title} — {value} {unit}"
    else:
        question = (f"Какое значение показателя «{title}» у ИУБиП "
                    f"в выпуске 2025?")
        answer = f"{title} — {value} {unit} [1]."
        text = f"{title} — {value} {unit}"
    add("extraction", question, answer,
        [chunk("ИУБиП, выпуск 2025: карточка организации",
               fact.get("block") or "", text)],
        ["official/monitoring/2025-card.md"],
        expected_value=None if fact.get("kind") == "profile" else value,
        source="мониторинг, карточка организации, выпуск 2025",
        split=split)

# 6.3. СПО: 20 показателей и структурные сведения.
spo_facts = [fact for fact in store_facts
             if fact["level"] == "SPO" and fact["release"] == 2025
             and fact.get("org_id") == "1971"]
for fact in spo_facts:
    code, title = fact["code"], fact["title"]
    value, unit = fact.get("value_text"), fact.get("unit") or ""
    metric = (fact.get("extra") or {}).get("metric")
    split = "train"
    if re.fullmatch(r"\d+\.\d+", str(code)):
        split = "test" if held_out("spo:" + code, 20, 4) else "train"
        question = (f"Какое значение показателя «{title}» у ИУБиП "
                    f"в СПО в выпуске 2025?")
        answer = f"В СПО значение показателя «{title}» — {value} {unit} [1]."
        if fact.get("subject_value") is not None:
            answer += (f" По субъекту РФ — {fmt(fact['subject_value'])} "
                       f"{unit}{end(unit)}")
        if fact.get("rf_value") is not None:
            answer += f" По РФ — {fmt(fact['rf_value'])} {unit}{end(unit)}"
        if fact.get("place") is not None:
            answer += (f" Место в области — {fact['place']} "
                       f"из {fact.get('place_total')}.")
        if fact.get("median_org") is not None:
            answer += (f" Медиана по организациям — "
                       f"{fmt(fact['median_org'])} {unit}{end(unit)}")
        text = f"{code}. {title} — {value} {unit}"
        if fact.get("subject_value") is not None:
            text += f"; по субъекту РФ — {fmt(fact['subject_value'])} {unit}"
        if fact.get("rf_value") is not None:
            text += f"; по РФ — {fmt(fact['rf_value'])} {unit}"
        if fact.get("place") is not None:
            text += f"; место — {fact['place']} из {fact.get('place_total')}"
        if fact.get("median_org") is not None:
            text += f"; медиана — {fmt(fact['median_org'])} {unit}"
        add("extraction", question, answer,
            [chunk("ИУБиП, СПО, выпуск 2025", fact.get("block") or "", text)],
            ["official/monitoring/spo/2025-iubip.md"],
            expected_value=value,
            source="мониторинг СПО, выпуск 2025",
            split=split)
        median = as_float(fact.get("median_org"))
        number = as_float(value)
        if median is not None and number is not None and abs(number - median) > 1e-9:
            add("comparison",
                f"На сколько значение показателя «{title}» у ИУБиП "
                f"в СПО отличается от медианы по организациям?",
                f"Разница — {fmt(number - median)} {unit}: у ИУБиП "
                f"{value} {unit}, медиана {fmt(median)} {unit} [1].",
                [chunk("ИУБиП, СПО, выпуск 2025", fact.get("block") or "", text)],
                ["official/monitoring/spo/2025-iubip.md"],
                operands=[value, fmt(median)],
                source="мониторинг СПО, выпуск 2025",
                split=split)
    elif code.startswith("info."):
        question = f"Что известно о «{title}» ИУБиП в СПО?"
        answer = f"В карточке СПО «{title}»: {value} [1]."
        text = f"{title}: {value}"
        add("extraction", question, answer,
            [chunk("ИУБиП, СПО, выпуск 2025: общие сведения",
                   "Общие сведения", text)],
            ["official/monitoring/spo/2025-iubip.md"],
            source="мониторинг СПО, выпуск 2025",
            split=split)
    elif code.startswith("branch."):
        metric_title = METRIC_TITLES.get(metric, metric or "показатель")
        question = (f"Каково значение показателя «{metric_title}» "
                    f"по отрасли «{title}» у ИУБиП в СПО?")
        answer = (f"По отрасли «{title}» {metric_title} — "
                  f"{value} {unit} [1].")
        text = f"Отрасль «{title}»: {metric_title} — {value} {unit}"
        add("extraction", question, answer,
            [chunk("ИУБиП, СПО, выпуск 2025",
                   "Распределение контингента по отраслям наук", text)],
            ["official/monitoring/spo/2025-iubip.md"],
            expected_value=value,
            source="мониторинг СПО, выпуск 2025",
            split=split)
    elif fact.get("block", "").startswith("Укрупнённые") and metric:
        phrase = {"city": "в городе", "region": "в регионе", "rf": "в РФ"}
        where = phrase.get(metric, "")
        question = (f"Сколько организаций СПО {where} реализуют "
                    f"УГПС {code} «{title}»?")
        answer = (f"По УГПС {code} «{title}» организаций {where} — "
                  f"{value} {unit} [1].")
        text = f"УГПС {code} «{title}»: организаций {where} — {value} {unit}"
        add("extraction", question, answer,
            [chunk("ИУБиП, СПО, выпуск 2025: УГПС",
                   "Укрупнённые группы профессий и специальностей", text)],
            ["official/monitoring/spo/2025-iubip.md"],
            expected_value=value,
            source="мониторинг СПО, выпуск 2025",
            split=split)
    else:
        question = (f"Какое значение показателя «{title}» у ИУБиП "
                    f"в СПО в выпуске 2025?")
        answer = f"{title} — {value} {unit} [1]."
        text = f"{title} — {value} {unit}"
        add("extraction", question, answer,
            [chunk("ИУБиП, СПО, выпуск 2025", fact.get("block") or "", text)],
            ["official/monitoring/spo/2025-iubip.md"],
            expected_value=value,
            source="мониторинг СПО, выпуск 2025",
            split=split)

# 6.4. E-показатели ИУБиП: значение и медианы (в региональной таблице
# медиан нет, поэтому вопрос отдельный, а не дубль региональной строки).
for fact in store_facts:
    if not (fact["level"] == "VO" and fact["release"] == 2025
            and fact.get("section") == "II" and fact.get("org_id") == "1971"
            and fact.get("kind") == "value" and fact.get("year") == 2025):
        continue
    title, value, unit = fact["title"], fact.get("value_text"), fact.get("unit") or ""
    parts = [f"{title} — {value} {unit}"]
    if fact.get("median_region") is not None:
        parts.append(f"медиана региона — {fmt(fact['median_region'])} {unit}")
    if fact.get("median_rf") is not None:
        parts.append(f"медиана РФ — {fmt(fact['median_rf'])} {unit}")
    if fact.get("median_department") is not None:
        parts.append(f"медиана ведомства — {fmt(fact['median_department'])} {unit}")
    if fact.get("place") is not None:
        parts.append(f"место — {fact['place']} из {fact.get('place_total')}")
    text = "; ".join(parts)
    answer = f"У ИУБиП показатель «{title}»: {value} {unit} [1]."
    if fact.get("median_region") is not None:
        answer += (f" Медиана региона — {fmt(fact['median_region'])} "
                   f"{unit}{end(unit)}")
    if fact.get("median_rf") is not None:
        answer += (f" Медиана РФ — {fmt(fact['median_rf'])} "
                   f"{unit}{end(unit)}")
    if fact.get("place") is not None:
        answer += f" Место — {fact['place']} из {fact.get('place_total')}."
    add("extraction",
        f"Какое значение показателя «{title}» у ИУБиП в выпуске 2025 "
        f"и каковы медианы?",
        answer,
        [chunk("ИУБиП, выпуск 2025: E-показатели",
               "Показатели мониторинга эффективности", text)],
        ["official/monitoring/region-2025.md"],
        expected_value=value,
        source="мониторинг, E-показатели, выпуск 2025",
        split="test")

# 6.5. Отказы по структурированным фактам: год не тот, но число рядом.
# Это главный провал первого прогона — адаптер брал число из контекста.
for fact in store_facts:
    if not (fact["level"] == "VO" and fact["release"] == 2025
            and fact.get("section") == "III" and fact.get("org_id") == "1971"
            and fact.get("kind") == "value"):
        continue
    code, title = fact["code"], fact["title"]
    value, unit = fact.get("value_text"), fact.get("unit") or ""
    text = f"{code}. {title} — {value} {unit}".strip()
    for year in ("2026", "2027", "2024"):
        add("refusal",
            f"Какое значение показателя «{title}» у ИУБиП в {year} году?",
            NO_ANSWER,
            [chunk("ИУБиП, выпуск 2025: направления деятельности",
                   fact.get("block") or "", text)],
            ["official/monitoring/2025-directions.md"],
            refusal_kind="year_mismatch",
            split="test" if (year == "2027" and held_out("ref-dir27:" + code, 10, 2))
            else "train")

for fact in store_facts:
    if not (fact["level"] == "SPO" and fact["release"] == 2025
            and fact.get("org_id") == "1971"
            and re.fullmatch(r"\d+\.\d+", str(fact.get("code") or ""))):
        continue
    code, title = fact["code"], fact["title"]
    value, unit = fact.get("value_text"), fact.get("unit") or ""
    text = f"{code}. {title} — {value} {unit}"
    for year in ("2026", "2027", "2024"):
        add("refusal",
            f"Какое значение показателя «{title}» у ИУБиП в СПО в {year} году?",
            NO_ANSWER,
            [chunk("ИУБиП, СПО, выпуск 2025", fact.get("block") or "", text)],
            ["official/monitoring/spo/2025-iubip.md"],
            refusal_kind="year_mismatch",
            split="test" if (year == "2026" and held_out("ref-spo26:" + code, 10, 2))
            else "train")

for fact in store_facts:
    if not (fact["level"] == "VO" and fact["release"] == 2025
            and fact.get("section") == "II" and fact.get("org_id") == "1971"
            and fact.get("kind") == "value" and fact.get("year") == 2025):
        continue
    title, value, unit = fact["title"], fact.get("value_text"), fact.get("unit") or ""
    text = f"{title} — {value} {unit}"
    for year in ("2026", "2027", "2024"):
        add("refusal",
            f"Какое значение показателя «{title}» у ИУБиП в {year} году?",
            NO_ANSWER,
            [chunk("ИУБиП, выпуск 2025: E-показатели",
                   "Показатели мониторинга эффективности", text)],
            ["official/monitoring/region-2025.md"],
            refusal_kind="year_mismatch",
            split="train" if year != "2027" else "test")

# ---------- запись -------------------------------------------------------
OUT_EVAL.mkdir(parents=True, exist_ok=True)
(OUT_EVAL / "questions.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
    encoding="utf-8")

for split in ("train", "dev", "test"):
    part = [e for e in examples if e["split"] == split]
    path = OUT_KAGGLE / f"{split}.jsonl"
    path.write_text(
        "\n".join(json.dumps({"messages": e["messages"]}, ensure_ascii=False)
                  for e in part) + "\n" if part else "",
        encoding="utf-8")

report = ["СБОРКА ВОПРОСОВ И ДАТАСЕТА", ""]
for split in ("train", "dev", "test"):
    by_task = {t: n for (s, t), n in sorted(counts.items()) if s == split}
    report.append(f"{split}: {sum(by_task.values())} — " +
                  ", ".join(f"{t} {n}" for t, n in by_task.items()))
report += ["", f"всего вопросов: {len(rows)}",
           "held-out внутри документов: 8 показателей направлений и "
           "4 показателя СПО (только test)",
           f"отброшено проверкой «есть во фрагменте»: {len(dropped)}", ""]
for item in dropped[:20]:
    report.append(f"  - [{item['task']}] {item['question']} | нет: {item['missing']}")
(OUT_KAGGLE / "dataset-report.txt").write_text(
    "\n".join(report) + "\n", encoding="utf-8")

print("\n".join(report))
