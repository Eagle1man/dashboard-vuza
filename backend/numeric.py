# -*- coding: utf-8 -*-
"""
Разбор чисел для публикации.

Правило ТЗ: одно значение не обязано быть float. Деньги и точные
десятичные хранятся канонической строкой Decimal (fact_metric.value_exact),
а REAL-поле остаётся производным — графики и сортировка им удобны.
Значение, которое не разбирается как число, публиковать нельзя:
оно уходит в проверки как ошибка, а не округляется молча.
"""
import re
from decimal import Decimal, InvalidOperation

_FULL_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?$")


def _normalize(s: str) -> str:
    """Приводит русский/международный числовой текст к Decimal-строке."""
    t = s.replace("\xa0", " ").replace("\u202f", " ").strip()
    t = re.sub(r"\s+", "", t)
    if "," in t and "." in t:
        # Кто-то писал «1,234.56», кто-то «1.234,56» —_decimal-разделитель
        # там, где он стоит последним.
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    return t


def to_decimal(value) -> Decimal | None:
    """Decimal или None, если это не число.

    Принимает числа, строки вида «1 234,56», «87%», «(1 200)»
    (в скобках — бухгалтерский минус). Возвращает None для всего, что
    не удалось разобрать однозначно: вызывающий обязан отнестись к этому
    как к ошибке данных, а не подставить ноль.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    s = str(value).strip()
    if not s:
        return None

    negative = False
    if len(s) >= 2 and s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    t = _normalize(s)
    # Снять хвостовые единицы измерения: «87 %», «12,5 тыс. руб.».
    if not _FULL_RE.match(t):
        t2 = re.sub(r"[%°]$", "", t)
        t2 = re.sub(r"[a-zа-яё.,]*$", "", t2)
        t = _normalize(t2)
    if not _FULL_RE.match(t):
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return -d if negative else d


def exact(value: Decimal) -> str:
    """Каноническое десятичное представление без потери точности."""
    if value is None:
        return ""
    # format(..., 'f') не даёт экспоненты: 1E+3 станет «1000».
    s = format(value.normalize(), "f")
    return s


def to_float(value: Decimal) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (OverflowError, ValueError):
        return None
    return f
