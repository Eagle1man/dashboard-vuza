# -*- coding: utf-8 -*-
"""Проверка ответа на выдуманные числа и ссылки.

Зачем. В промпте написано «цифры только из фрагментов» и «ссылку [n] на
каждое утверждение». Модель 1.5B это правило нарушает: на вопрос об
итогах успеваемости она ответила «У — от 35% до 45%, СБ — 85», хотя
таких чисел в документе нет вовсе. Просьба к модели не заменяет
проверку — проверять надо результат, а не намерение.

Что делает проверка:

  1) вынимает из ответа числа и сверяет их с числами фрагментов,
     которые реально были отданы модели;
  2) предложение с неподтверждённым числом удаляется целиком: вместе с
     ним уходит и выдумка, и вывод, построенный на ней;
  3) ссылки [n] проверяются на диапазон — ссылка на несуществующий
     фрагмент снимается;
  4) если после чистки ничего не осталось, вызывающий обязан показать
     найденные фрагменты вместо ответа (текст возвращается пустым).

Числа сравниваются с допуском 0,5%: «87,4» и «87,40» — одно и то же,
а «35» и «45» — разные числа. Допуск не спасает от выдумки, но убирает
ложные срабатывания на форматировании.

Отчёт (сколько убрано, какие числа не подтвердились) возвращается
вместе с текстом и уходит в meta ответа — чтобы это было видно, а не
растворялось в тишине.
"""
import re

# Число: либо с разрядами через пробел (2 946), либо обычное (2946),
# с дробной частью через точку или запятую. Пробел-разделитель
# допускается только между группами по три цифры, иначе «14 15» в
# перечислении склеилось бы в 1415.
_NUM = re.compile(
    r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?"
    r"|\d+(?:[.,]\d+)?")

_CITE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")

_SENT_END = re.compile(r"(?<=[.!?])\s+")


def _to_float(raw: str) -> float | None:
    s = (raw.replace("\u00a0", "").replace("\u202f", "")
         .replace(" ", "").replace(",", "."))
    s = s.rstrip(".")
    try:
        return float(s)
    except ValueError:
        return None


def extract_numbers(text: str) -> list[tuple[float, str]]:
    """Числа из текста: [(значение, как написано)]."""
    out = []
    for m in _NUM.finditer(text or ""):
        raw = m.group(0).strip()
        value = _to_float(raw)
        if value is not None:
            out.append((value, raw))
    return out


def _present(value: float, pool: list[float]) -> bool:
    for v in pool:
        if value == v:
            return True
        scale = max(abs(v), abs(value), 1e-9)
        if abs(v - value) <= scale * 0.005:
            return True
    return False


def verify(answer: str, context: str, chunk_count: int) -> tuple[str, dict]:
    """Чистит ответ. Возвращает (текст, отчёт).

    Пустой текст означает «показывать нечего»: все утверждения с числами
    не подтвердились. Решение, что показать вместо него, принимает
    вызывающий — у него есть список фрагментов.
    """
    report = {
        "ok": True,
        "removed": [],            # удалённые предложения
        "repeated": [],           # повторы, снятые как шум
        "unsupported_numbers": [],  # числа, которых нет во фрагментах
        "bad_citations": [],      # ссылки за пределы списка фрагментов
        "cited": [],              # какие фрагменты ответ реально использует
        "checked_numbers": 0,
    }
    if not answer or not answer.strip():
        return answer, report
    if not context:
        return answer, report

    pool = [value for value, _raw in extract_numbers(context)]

    # --- ссылки ---------------------------------------------------------
    cited = set()
    for m in _CITE.finditer(answer):
        for part in re.split(r"[,;]", m.group(1)):
            part = part.strip()
            if not part.isdigit():
                continue
            n = int(part)
            if 1 <= n <= chunk_count:
                cited.add(n)
            else:
                report["bad_citations"].append(n)
    report["cited"] = sorted(cited)

    def _clean_citations(sentence: str) -> str:
        def repl(m):
            keep = [p.strip() for p in re.split(r"[,;]", m.group(1))
                    if p.strip().isdigit() and 1 <= int(p.strip()) <= chunk_count]
            return "[" + ",".join(keep) + "]" if keep else ""
        return _CITE.sub(repl, sentence)

    # --- числа ----------------------------------------------------------
    kept_lines = []
    seen = set()
    for line in answer.splitlines():
        if not line.strip():
            kept_lines.append("")
            continue
        # Номер пункта списка — не факт из документа: «1.» в начале
        # строки не должен считаться числом и топить предложение.
        prefix = ""
        m = re.match(r"^\s*(\d+[.)])\s+(.*)$", line)
        if m:
            prefix, line = m.group(1) + " ", m.group(2)
        kept_sentences = []
        for sentence in _SENT_END.split(line.strip()):
            # Ссылки вынимаем перед поиском чисел: «[1,7]» — это две
            # ссылки, а не дробное число 1,7.
            plain = _CITE.sub(" ", sentence)
            key = re.sub(r"\s+", " ", plain).strip().lower().rstrip(".!?")
            if key and key in seen:
                # 1.5B уходит в повтор: один и тот же пункт идёт 10 раз
                # подряд. Повтор не добавляет утверждений, но занимает
                # весь ответ и вытесняет остальное.
                report["repeated"].append(sentence.strip())
                continue
            if key:
                seen.add(key)
            nums = extract_numbers(plain)
            if not nums:
                kept_sentences.append(_clean_citations(sentence))
                continue
            report["checked_numbers"] += len(nums)
            bad = [raw for value, raw in nums if not _present(value, pool)]
            if bad:
                report["removed"].append(sentence.strip())
                for raw in bad:
                    if raw not in report["unsupported_numbers"]:
                        report["unsupported_numbers"].append(raw)
                continue
            kept_sentences.append(_clean_citations(sentence))
        kept = " ".join(s for s in kept_sentences if s.strip())
        kept_lines.append((prefix + kept) if kept.strip() else "")

    text = "\n".join(kept_lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    report["ok"] = not (report["removed"] or report["bad_citations"])
    return text, report


def note(report: dict) -> str:
    """Пояснение к ответу: что проверка убрала и почему."""
    parts = []
    if report.get("removed"):
        nums = report.get("unsupported_numbers") or []
        tail = (": " + ", ".join(nums[:6])) if nums else ""
        parts.append(
            f"Проверка убрала {len(report['removed'])} предложение(й) — "
            f"в них были числа, которых нет в найденных документах{tail}.")
    if report.get("bad_citations"):
        parts.append("Сняты ссылки на несуществующие фрагменты: "
                     + ", ".join(f"[{n}]" for n in report["bad_citations"]) + ".")
    if report.get("repeated"):
        parts.append(f"Убраны повторы одного и того же пункта: "
                     f"{len(report['repeated'])}.")
    if not report.get("cited"):
        parts.append("Модель не поставила ссылки [n] на фрагменты — "
                     "сверьте утверждения со списком источников ниже.")
    if not parts:
        return ""
    return "\n\nЗамечание проверки: " + " ".join(parts)
