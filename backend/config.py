# -*- coding: utf-8 -*-
"""
Пути, настройки и модели. Один файл, чтобы установщик и бэкенд
читали одно и то же место.
"""
import os
from pathlib import Path

# Корень проекта — на уровень выше backend/
ROOT = Path(__file__).resolve().parent.parent

WEB_DIR = ROOT / "web"
KNOWLEDGE_DIR = ROOT / "knowledge"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"

DB_PATH = DATA_DIR / "dashboard.db"
BACKUP_DIR = DATA_DIR / "backup"

# Локальный сервер Ollama.
# Именно 127.0.0.1, а не localhost: при выключенной Ollama имя хоста
# дополнительно тратит около двух секунд на попытку разрешения IPv6
# (::1) — а такие проверки идут при каждой загрузке страницы.
OLLAMA_URL = os.environ.get("RECTOR_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT = float(os.environ.get("RECTOR_OLLAMA_TIMEOUT", "300"))

# Наш бэкенд
API_HOST = "127.0.0.1"
API_PORT = int(os.environ.get("RECTOR_API_PORT", "8765"))

# --- Параметры чанкинга -------------------------------------------------
# Заданы в токенах, а не в символах: у русского текста символ/токен
# соотношение примерно 2:1, и резать по символам нестабильно.
CHUNK_TOKENS = int(os.environ.get("RECTOR_CHUNK_TOKENS", "700"))
CHUNK_OVERLAP = int(os.environ.get("RECTOR_CHUNK_OVERLAP", "120"))

# Сколько фрагментов уходит в LLM
RETRIEVE_TOP_K = int(os.environ.get("RECTOR_TOP_K", "20"))
CONTEXT_CHUNKS = int(os.environ.get("RECTOR_CONTEXT_CHUNKS", "6"))

# Коэффициент слияния RRF (Reciprocal Rank Fusion)
RRF_K = 60

# Порог схожести, ниже которого векторный канал считается бесполезным.
# Смысл: если ни один фрагмент не близок к вопросу по вектору, вопрос
# не про этот корпус — и честнее сказать «не найдено», чем подсунуть
# нерелевантное. Эмбеддинги нормализованы, поэтому косинус в 0..1.
MIN_COSINE = float(os.environ.get("RECTOR_MIN_COSINE", "0.42"))

# Вес переранжирования по покрытию вопроса. Сопоставим с величинами
# RRF (порядка 0.02–0.03), чтобы заметно влиять, но не перебивать
# саму релевантность: покрытие — добавка к рангу, не замена ему.
COVERAGE_WEIGHT = float(os.environ.get("RECTOR_COVERAGE_WEIGHT", "0.04"))

# Пороги корректирующего RAG: решаем, годится ли найденный контекст
# ДО того, как модель начнёт по нему писать.
#   >= JUDGE_OK   — отвечаем
#   >= JUDGE_LOW  — один раз переформулируем запрос и ищем шире
#   <  JUDGE_LOW  — честно говорим, что не нашли
# Пороги заданы по шкале, где доверенный вектор даёт косинус, а при
# отключённом векторе — среднее покрытие вопроса фрагментами.
JUDGE_OK = float(os.environ.get("RECTOR_JUDGE_OK", "0.55"))
JUDGE_LOW = float(os.environ.get("RECTOR_JUDGE_LOW", "0.30"))
# Штраф, если всё найденное из одного документа при посредственном
# покрытии: поиск выдаёт соседние куски одного файла, а нужный
# документ не нашёлся.
JUDGE_NARROW_PENALTY = float(os.environ.get("RECTOR_JUDGE_NARROW", "0.8"))

# --- Модели -------------------------------------------------------------
# Подбираются автоматически по железу, см. autoconfig.py.
# Здесь — только варианты, из которых он выбирает.
MODELS_TINY = ["qwen2.5:1.5b"]
MODELS_SMALL = ["qwen2.5:3b"]
MODELS_MEDIUM = ["qwen2.5:7b"]
# Большая модель для объяснений (ТЗ §13). По договорённости с заказчиком
# подключается при размещении проекта на ВМ: MODEL_LARGE=<тег> либо
# meta('llm_model_large'). Пока не подключена — объяснения честно
# сообщают об этом, детерминированные проверки работают без неё.
MODELS_LARGE = ["qwen2.5:14b"]

EMBED_TINY = ["nomic-embed-text"]
EMBED_MEDIUM = ["bge-m3"]

# Сид демонстрационных метрик. Тот же, что в web/js/data.js,
# чтобы числа на экране и в базе совпадали.
DEMO_SEED = 2026

SUPPORTED_EXT = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".md", ".txt",
                 ".html", ".htm", ".csv"}

# --- Конвейер загрузки (ТЗ 2.0) ----------------------------------------
# Лимит размера файла: проверяется по ходу чтения, не после (ТЗ §6).
MAX_UPLOAD_MB = int(os.environ.get("RECTOR_MAX_UPLOAD_MB", "50"))
# Сколько строк сырых значений хранится на одну загрузку (ТЗ §5.1).
MAX_RAW_ROWS = int(os.environ.get("RECTOR_MAX_RAW_ROWS", "6000"))
# Допуск проверки «сумма частей = итог» (ТЗ §10), доля от суммы.
SUM_TOLERANCE = float(os.environ.get("RECTOR_SUM_TOLERANCE", "0.01"))
# Ограничение числа неудачных попыток входа (ТЗ §12).
# Выключается RECTOR_LOGIN_LIMIT=0 — решение остаётся за владельцем.
LOGIN_LIMIT_ON = os.environ.get("RECTOR_LOGIN_LIMIT", "1") != "0"
LOGIN_MAX_ATTEMPTS = int(os.environ.get("RECTOR_LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_WINDOW_MIN = int(os.environ.get("RECTOR_LOGIN_WINDOW_MIN", "15"))

DEMO_BANNER = "демонстрационные данные"