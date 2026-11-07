# -*- coding: utf-8 -*-
"""Общие правила для тестов.

Зачем это здесь. Часть тестов подменяет `config.DB_PATH` на временную
базу, чтобы не трогать рабочую. Под pytest такая подмена переживала
тест: `test_auth` оставлял путь на пустую temp-базу, и следующие по
алфавиту тесты поиска честно ничего не находили — при запуске по
одному они проходили. Фикстура возвращает исходный путь после каждого
теста, чтобы порядок запуска не влиял на результат.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

_ORIGINAL_DB_PATH = config.DB_PATH


@pytest.fixture(autouse=True)
def _restore_db_path():
    yield
    config.DB_PATH = _ORIGINAL_DB_PATH
