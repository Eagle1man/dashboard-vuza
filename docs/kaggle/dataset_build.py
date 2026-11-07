# -*- coding: utf-8 -*-
"""Собирает датасет для QLoRA из всей базы знаний мониторинга.

Главное правило: **ответ обязан быть в контексте**. После сборки каждый
пример проверяется — если значения нет в переданном фрагменте, пример
выбрасывается и попадает в отчёт. Так в датасет не просачивается
выдуманное число, и модель учится отвечать по фрагменту, а не по памяти.

    python docs/kaggle/dataset_build.py
    -> docs/kaggle/train.jsonl, docs/kaggle/dataset-report.txt
"""
import json
import pathlib
import re
import statistics

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
KB = ROOT / "knowledge" / "official" / "monitoring"
OUT = pathlib.Path(__file__).resolve().parent

SYSTEM = ("Ты — ассистент ректора Южного университета (ИУБиП). Отвечай "
          "кратко, по-русски, только по предоставленному фрагменту и "
          "всегда указывай источник. Если во фрагменте нет ответа — "
          "скажи, что данных нет, и не выдумывай числа.")

rows = []
dropped = []


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def add(question, context, answer, source, strict_value=None):
    """Добавляет пример, если ответ действительно есть во фрагменте."""
    check = norm(strict_value) if strict_value is not None else norm(answer)
    if check and check not in norm(context):
        dropped.append({"question": question[:90], "missing": check[:60],
                        "context": norm(context)[:90]})
        return
    rows.append({
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Вопрос: {question}\n\nФрагмент:\n{context}"},
            {"role": "assistant", "content": f"{answer}\n\nИсточник: {source}"},
        ]
    })


# ---------- 1. Детальные показатели ИУБиП ----------
DETAIL = re.compile(r"^- (.+?)(?: \((.+?)\))?: \*\*(.+?)\*\*$")
for year in ("2024", "2025"):
    path = KB / f"{year}-iubip.md"
    if not path.exists():
        continue
    source = f"мониторинг эффективности, выпуск {year}, карточка ИУБиП"
    for line in path.read_text(encoding="utf-8").splitlines():
        m = DETAIL.match(line.strip())
        if not m:
            continue
        title, unit, value = m.group(1), m.group(2) or "", m.group(3)
        unit_text = f" {unit}" if unit else ""
        context = line.strip().lstrip("- ")
        for question in (
            f"Какое значение показателя «{title}» у ИУБиП в выпуске {year}?",
            f"Назови значение показателя «{title}» по данным мониторинга {year}.",
            f"Чему равен показатель «{title}» у Южного университета (ИУБиП), выпуск {year}?",
        ):
            add(question, context, f"{value}{unit_text}.", source, strict_value=value)

# ---------- 2. Региональные таблицы: любая строка ----------
SECTION = re.compile(r"^## (E\.\d.*)$")
NUM_ROW = re.compile(r"^(\d+)\. (.+?) — (-?[\d.,]+)( ← ИУБиП)?$")

for year in ("2024", "2025"):
    path = KB / f"region-{year}.md"
    if not path.exists():
        continue
    source = f"мониторинг эффективности, выпуск {year}, вузы Ростовской области"
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        sec = SECTION.match(line.strip())
        if sec:
            current = sec.group(1)
            continue
        row = NUM_ROW.match(line.strip())
        if not row or not current:
            continue
        place, name, value, mark = row.groups()
        add(
            f"Какое значение показателя «{current}» у вуза «{name}» "
            f"в выпуске {year}?",
            f"{current}. Строка таблицы: {line.strip()}",
            f"{value}.",
            source,
            strict_value=value,
        )
        if mark:
            add(
                f"Какое место занимает ИУБиП по показателю «{current}» "
                f"среди вузов Ростовской области в выпуске {year}?",
                f"{current}. Строка таблицы: {line.strip()}",
                f"{place}-е место, значение {value}.",
                source,
                strict_value=value,
            )

# ---------- 3. Карточки других вузов ----------
CARD_LINE = re.compile(r"^- (.+?)(?: \((.+?)\))?: (.+)$")
cards = sorted((KB / "by-vuz").glob("*.md"))
for card in cards:
    year = "2025" if card.stem.endswith("-2025") else "2024"
    name = card.stem.rsplit("-", 1)[0]
    lines = card.read_text(encoding="utf-8").splitlines()
    vuz_title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), name)
    facts = [l for l in lines if CARD_LINE.match(l.strip()) and ":" in l][:12]
    for line in facts:
        m = CARD_LINE.match(line.strip().lstrip("- "))
        if not m:
            continue
        title, unit, value = m.group(1), m.group(2) or "", m.group(3)
        if not re.search(r"\d", value):
            continue
        add(
            f"Какое значение показателя «{title}» у вуза «{vuz_title}» "
            f"в выпуске {year}?",
            line.strip().lstrip("- "),
            f"{value}{(' ' + unit) if unit else ''}.",
            f"мониторинг эффективности, выпуск {year}, карточка вуза",
            strict_value=value.split(";")[0].strip(),
        )

# ---------- 4. Агрегаты региона ----------
for year in ("2024", "2025"):
    path = KB / f"region-aggregates-{year}.md"
    if not path.exists():
        continue
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip().lstrip("- ")
        if len(text) < 25 or not re.search(r"\d", text):
            continue
        add(
            f"Что известно по Ростовской области из мониторинга {year}: {text[:60]}?",
            text,
            f"{text}.",
            f"мониторинг эффективности, выпуск {year}, сводка по региону",
            strict_value=text[:40],
        )

# ---------- 5. Отказы ----------
for question, answer in [
    ("Сколько студентов в ИУБиП в 2027 году?",
     "В базе есть выпуски 2024 и 2025 годов; данных за 2027 год нет."),
    ("Какие показатели ИУБиП за 2026 год?",
     "Выпуска 2026 года в базе нет — последний доступный это 2025."),
    ("Какое место ИУБиП займёт в 2027 году по среднему баллу ЕГЭ?",
     "Прогнозов в документах нет, есть только фактические выпуски."),
]:
    rows.append({
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Вопрос: {question}\n\nФрагмент:\nДоступные выпуски: 2024, 2025."},
            {"role": "assistant", "content": f"{answer}\n\nИсточник: база знаний мониторинга"},
        ]
    })

(OUT / "train.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

report = [
    f"примеров: {len(rows)}",
    f"отброшено проверкой: {len(dropped)}",
    f"размер: {(OUT / 'train.jsonl').stat().st_size // 1024} КБ",
    f"карточек вузов: {len(cards)}",
    "",
    "Проверка «ответ есть во фрагменте» — обязательная. Отброшенные примеры:",
]
for item in dropped[:20]:
    report.append(f"  - {item['question']} | нет: {item['missing']}")
(OUT / "dataset-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")

print("\n".join(report[:4]))
print("отчёт:", OUT / "dataset-report.txt")
