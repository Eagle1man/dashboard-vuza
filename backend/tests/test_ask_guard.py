# -*- coding: utf-8 -*-
"""Заслонка «один вопрос за раз».

Зачем: пока модель считает минутами, второй вопрос молча вставал в
очередь к Ollama (OLLAMA_NUM_PARALLEL=1), браузер сдавался по таймауту,
и человек видел «сбой», не понимая, что его вопрос даже не начинался.
Честнее сразу ответить «занято» — и не держать очередь.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from backend import app as webapp  # noqa: E402


def test_second_question_is_rejected():
    with webapp._one_question_at_a_time():
        try:
            with webapp._one_question_at_a_time():
                raise AssertionError("второй вопрос пролез одновременно с первым")
        except HTTPException as exc:
            assert exc.status_code == 429, exc.status_code
            assert "уже отвечает" in exc.detail, exc.detail

    # После освобождения задавать вопросы снова можно.
    with webapp._one_question_at_a_time():
        pass


def test_lock_is_released_after_error():
    try:
        with webapp._one_question_at_a_time():
            raise RuntimeError("сломалось внутри")
    except RuntimeError:
        pass

    with webapp._one_question_at_a_time():
        pass
