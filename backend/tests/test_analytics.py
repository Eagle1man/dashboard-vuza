# -*- coding: utf-8 -*-
"""Витрина мониторинга: числа, места и отказы.

Главная проверка здесь — не «функция вернула число», а **совпадение с
текстом базы знаний построчно**. Витрина собрана из того же JSON, из
которого собран текст; если они разойдутся, ответ покажет одно число, а
ссылка [1] — другое. Это хуже отказа, потому что выглядит проверяемым.

Вторая проверка — отказы. Витрина обязана молчать там, где данных нет:
ответ про «похожий показатель» неотличим от факта.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import analytics  # noqa: E402

KB = ROOT / "knowledge" / "official" / "monitoring"


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    path = tmp_path_factory.mktemp("analytics") / "analytics.db"
    analytics.build(path, verbose=False)
    connection = analytics.connect(path)
    yield connection
    connection.close()


def _answer(conn, question):
    return analytics.answer(conn, question)


# --------------------------------------------------------------- совпадение

def _same_org(md_norm: str, db_norm: str) -> bool:
    """Один и тот же вуз, если совпало не менее 90% слов названия.

    Подстрокой сравнивать нельзя: источники разошлись в написании —
    в JSON вуз уже «…университет правосудия имени В.М. Лебедева», в
    тексте базы знаний ещё без «имени…». Это не ошибка числа, а
    расхождение двух артефактов; значения при этом совпадают.
    """
    md_tokens = set(md_norm.split())
    db_tokens = set(db_norm.split())
    if not md_tokens:
        return False
    return len(md_tokens & db_tokens) / len(md_tokens) >= 0.9


def test_region_rows_match_knowledge_base(conn):
    """Каждая строка региональной таблицы есть в витрине — тот же вуз и число.

    Допуск 0,06 — не поблажка: в тексте базы знаний крупные значения
    округлены до одного знака (14 457,4), в источнике они с двумя
    (14 457,43). Это наша же генерация текста, и расхождение не должно
    считаться ошибкой данных — но и скрывать его нельзя, поэтому допуск
    ровно на округление, а не «примерно равно».
    """
    import re

    row_re = re.compile(r"^(\d+)\.\s+(.+?)\s+—\s+(-?[\d.,]+)(?:\s+←.*)?$")
    checked = mismatched = 0
    problems = []
    for year in ("2024", "2025"):
        code = None
        for line in (KB / f"region-{year}.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                m = re.match(r"##\s+E\.?(\d)", line)
                code = f"E{m.group(1)}" if m else None
                continue
            row = row_re.match(line.strip())
            if not row or not code:
                continue
            checked += 1
            name_norm = analytics.norm_name(row.group(2))
            value = analytics.norm_num(row.group(3))
            hits = conn.execute(
                "SELECT z.name, v.value FROM value v JOIN vuz z ON z.vuz_id = v.vuz_id"
                " AND z.level = v.level WHERE v.level = ? AND v.release = ?"
                " AND v.code = ? AND v.year = ?",
                ("ВО", int(year), code, int(year))).fetchall()
            same_vuz = [h["value"] for h in hits
                        if _same_org(name_norm, analytics.norm_name(h["name"]))]
            if not same_vuz:
                mismatched += 1
                problems.append((year, code, name_norm[:40], "вуза нет в витрине"))
                continue
            if not any(abs(v - value) <= 0.06 for v in same_vuz):
                mismatched += 1
                problems.append((year, code, name_norm[:40], value, same_vuz[:2]))
    assert checked == 325, f"строк проверено {checked}, ожидалось 325 (160 + 165)"
    assert not mismatched, f"расхождений {mismatched}, напр. {problems[:5]}"


def test_vitrina_has_more_rows_than_published_tables(conn):
    """В витрине все вузы, а в таблицах базы знаний — только верх списка.

    Региональные таблицы публикуют 20 строк на показатель, источник
    содержит все организации области. Витрина обязана быть не беднее
    текста, иначе часть ответов останется без чисел.
    """
    for year, published in (("2024", 160), ("2025", 165)):
        got = conn.execute(
            "SELECT COUNT(*) FROM value WHERE level='ВО' AND release=? AND year=?",
            (int(year), int(year))).fetchone()[0]
        assert got >= published, f"выпуск {year}: в витрине {got} < {published}"


def test_iubip_value_matches_card(conn):
    """60,74 за 2025 — из карточки ИУБиП, не «примерно столько»."""
    data = _answer(conn, "Какое значение показателя «E.1 Средний балл ЕГЭ» "
                         "у ИУБиП в выпуске 2025?")
    assert data and data["kind"] == "value", data
    assert data["numbers"][0] == pytest.approx(60.74)


# ------------------------------------------------------------------ разбор

def test_alias_leads_to_main_university_not_branch(conn):
    """«ЮФУ» — головной вуз, а не первый филиал с тем же названием.

    Ошибка была настоящая: сокращение находило филиал в Новошахтинске
    (60,41) вместо ЮФУ (72,96) — правдоподобное число не того вуза.
    """
    data = _answer(conn, "А у ЮФУ сколько Е.1?")
    assert data, "витрина не узнала ЮФУ"
    assert "филиал" not in data["text"].lower(), data["text"]
    assert data["numbers"][0] == pytest.approx(72.96)


def test_comparison_subtracts_the_other_university(conn):
    """Сравнение считает разность, а не отдаёт значение ИУБиП."""
    data = _answer(
        conn,
        "На сколько значение показателя «E.1 Средний балл ЕГЭ» у ИУБиП "
        "отличается от значения у вуза «Федеральное государственное "
        "автономное образовательное учреждение высшего образования "
        "«Южный федеральный университет»» в выпуске 2025?")
    assert data and data["kind"] == "compare_vuz", data
    iubip, other, diff = data["numbers"]
    assert iubip == pytest.approx(60.74)
    assert other == pytest.approx(72.96)
    assert diff == pytest.approx(-12.22, abs=0.01)


def test_rank_matches_source_card(conn):
    """Место по НИОКР: 5 из 38 — как в карточке ИУБиП."""
    data = _answer(conn, "Какое место ИУБиП по объёму НИОКР в 2025?")
    assert data and data["kind"] == "rank", data
    assert "5 из 38" in data["text"], data["text"]


def test_dynamics_subtracts_years(conn):
    data = _answer(conn, "Покажи динамику доходов на одного НПР у ИУБиП "
                         "с 2024 по 2025")
    assert data and data["kind"] == "years", data
    y2024, y2025, diff = data["numbers"]
    assert y2024 == pytest.approx(4578.85)
    assert y2025 == pytest.approx(4425.73)
    assert diff == pytest.approx(-153.12, abs=0.01)


def test_spo_uses_its_own_card(conn):
    data = _answer(conn, "Сколько студентов СПО у ИУБиП?")
    assert data and data["kind"] == "spo", data
    assert data["numbers"][0] == pytest.approx(1131)
    assert "28 из 141" in data["text"], data["text"]


# ------------------------------------------------------------------ отказы

@pytest.mark.parametrize("question", [
    "Сколько публикаций в Scopus у ИУБиП в 2025?",
    "Каков объём эндаумент-фонда ИУБиП?",
    "Как проходит государственная итоговая аттестация?",
    "Какие документы нужны для перевода на другой факультет?",
    "Сколько студентов обучается в ИУБиП?",
    "Что сказано в положении об оплате обучения?",
    # Года нет в источнике — подставлять ближайший нельзя.
    "Каков средний балл ЕГЭ у ИУБиП в 2026 году?",
    # Доля — не количество: «сколько иностранных студентов» ждёт число.
    "Сколько иностранных студентов в ИУБиП в 2025?",
])
def test_refuses_when_data_absent(conn, question):
    """Нет данных или вопрос не числовой — витрина молчит.

    «Сколько студентов обучается в ИУБиП?» без уточнения «СПО» — это
    вопрос к карточке ВО, а не к витрине показателей: пусть отвечает
    поиск по документам, где есть 1 350.
    """
    assert _answer(conn, question) is None


def test_share_question_gets_share(conn):
    """Про долю — отвечаем долей, а не отказываем."""
    data = _answer(conn, "Какова доля иностранных студентов в ИУБиП в 2025?")
    assert data and data["code"] == "E3", data
    assert data["numbers"][0] == pytest.approx(4.36)


def test_unknown_vuz_is_not_guessed(conn):
    """Вуз, которого нет в области, не подменяется похожим."""
    data = _answer(conn, "Какое значение показателя «E.1 Средний балл ЕГЭ» "
                         "у вуза «Московский государственный университет» "
                         "в выпуске 2025?")
    assert data is None


def test_renamed_university_is_found(conn):
    """Источник дополнил название, текст базы знаний — нет.

    В JSON вуз уже «…университет правосудия имени В.М. Лебедева», в
    вопросе (из текста) — без вставки. Значение обязано найтись: иначе
    четыре вопроса из dev уходили в отказ, хотя данные есть.
    """
    data = _answer(conn, "Какое значение показателя «E.1 Средний балл ЕГЭ» "
                         "у вуза «Ростовский филиал Федерального "
                         "государственного бюджетного образовательного "
                         "учреждения высшего образования \"Российский "
                         "государственный университет правосудия\" "
                         "(г. Ростов-на-Дону)» в выпуске 2024?")
    assert data, "витрина не нашла вуз с дополненным названием"
    assert data["numbers"][0] == pytest.approx(70.92)
