# -*- coding: utf-8 -*-
"""Агенты аналитической системы «Аналитик ректора».

Роутер, семь специалистов по блокам дашборда, сводный аналитик и критик.
Объявления — в registry.py, оркестровка — в backend/crew.py.
"""
from . import critic, registry  # noqa: F401

__all__ = ["registry", "critic"]