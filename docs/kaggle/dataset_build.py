# -*- coding: utf-8 -*-
"""Собирает датасет для QLoRA из базы знаний мониторинга.

Логика: ответ должен выводиться из фрагмента, а не вспоминаться. Поэтому
в каждом примере есть контекст (строка из базы), вопрос по нему и ответ
с источником. Отдельный блок — отказы: вопрос про год, которого нет,
модель обязана не выдумывать.

    python docs/kaggle/dataset_build.py
    -> docs/kaggle/train.jsonl
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
KB = ROOT / "knowledge" / "official" / "monitoring"
OUT = pathlib.Path(__file__).resolve().parent / "train.jsonl"

SYSTEM = ("Ты — ассистент ректора Южного университета (ИУБиП). Отвечай "
          "кратко, по-русски, только по предоставленному фрагменту и "
          "всегда указывай источник. Если во фрагменте нет ответа — "
          "скажи, что данных нет, и не выдумывай числа.")

UNIT_HINT = {
    "балл": "балл", "%.*": "%", "чел.": "чел.", "тыс. руб.": "тыс. руб.",
    "ед.": "ед.", "тыс.руб.": "тыс. руб.", "м2": "м²", "м3": "м³",
}

rows = []


def add(question, context, answer, source):
    rows.append({
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Вопрос: {question}\n\nФрагмент:\n{context}"},
            {"role": "assistant", "content": f"{answer}\n\nИсточник: {source}"},
        ]
    })


# --- Детальные показатели ИУБиП ---
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
        # Три формулировки одного факта: модель учится отвечать на разные
        # вопросы, а не запоминает одну строку.
        for question in (
            f"Какое значение показателя «{title}» у ИУБиП в выпуске {year}?",
            f"Назови значение показателя «{title}» по данным мониторинга {year}.",
            f"Чему равен показатель «{title}» у Южного университета (ИУБиП), выпуск {year}?",
        ):
            add(question, context, f"{value}{unit_text}.", source)

# --- Сравнение по региону ---
SECTION = re.compile(r"^## (E\.\d.*)$")
NUM_ROW = re.compile(r"^(\d+)\. (.+?) — (-?[\d.,]+)( ← ИУБиП)?$")

for year in ("2024", "2025"):
    path = KB / f"region-{year}.md"
    if not path.exists():
        continue
    source = f"мониторинг эффективности, выпуск {year}, вузы Ростовской области"
    current = None
    block = []
    for line in path.read_text(encoding="utf-8").splitlines():
        sec = SECTION.match(line.strip())
        if sec:
            current, block = sec.group(1), []
            continue
        row = NUM_ROW.match(line.strip())
        if not row or not current:
            continue
        place, name, value, mark = row.group(1), row.group(2), row.group(3), row.group(4)
        block.append(line.strip())
        if mark:
            add(
                f"Какое место занимает ИУБиП по показателю «{current}» "
                f"среди вузов Ростовской области в выпуске {year}?",
                f"{current}. Всего участников: {len(block)} в начале раздела.\n"
                + "\n".join(block[-5:]),
                f"{place}-е место, значение {value}.",
                source,
            )
    # Лидер по каждому показателю: первая строка раздела.
    current, first = None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        sec = SECTION.match(line.strip())
        if sec:
            current, first = sec.group(1), None
            continue
        row = NUM_ROW.match(line.strip())
        if row and current and first is None:
            first = (row.group(2), row.group(3))
            add(
                f"Какой вуз Ростовской области лучший по показателю "
                f"«{current}» в выпуске {year}?",
                line.strip(),
                f"{first[0]} — значение {first[1]}.",
                source,
            )

# --- Отказы: год, которого в базе нет ---
missing = [
    ("Сколько студентов в ИУБиП в 2027 году?",
     "В базе есть выпуски мониторинга 2024 и 2025 годов; 2027 года нет."),
    ("Какие показатели ИУБиП за 2026 год?",
     "Выпуска 2026 года в базе нет — последний доступный это 2025 год."),
    ("Какое место ИУБиП займёт в 2027 году по среднему баллу ЕГЭ?",
     "Прогнозов в документах нет, есть только фактические выпуски 2024 и 2025."),
]
for question, answer in missing:
    add(question, "Доступные выпуски: 2024, 2025.", answer,
        "база знаний мониторинга")

OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
               encoding="utf-8")
print(f"готово: {OUT}")
print(f"примеров: {len(rows)}")
print(f"размер: {OUT.stat().st_size // 1024} КБ")
