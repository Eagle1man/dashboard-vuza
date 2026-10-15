# -*- coding: utf-8 -*-
"""
Автоконфигуратор: сам выбирает модель по компьютеру.

Зачем. Компьютер ректора никто не измерял, а разница между 1.5b и 7b
не в качестве «поменьше/поменьше», а в том, что 7b на 8 ГБ ОЗУ
просто не загрузится — Ollama будет убивать процесс. Поэтому при
установке меряем реальное железо и записываем выбор в базу, откуда
его читают и бэкенд, и установщик.

Пороги подобраны с запасом: оценка — это суммарная доступная память
(ОЗУ + свободная VRAM), потому что модель в основном живёт в VRAM,
а при её отсутствии — в ОЗУ, и тогда нужно, чтобы ОЗУ хватало ещё
на систему.
"""
import ctypes
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from . import config


def total_ram_gb() -> float:
    """Всего физической памяти, ГБ."""
    try:
        if sys.platform == "win32":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
                def __init__(self):
                    super().__init__()
                    self.dwLength = ctypes.sizeof(self)
            st = MEMORYSTATUSEX()
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullTotalPhys / (1024 ** 3)
    except Exception:
        pass
    try:
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / (1024 ** 3)
    except Exception:
        pass
    return 0.0


def available_ram_gb() -> float:
    try:
        if sys.platform == "win32":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
                def __init__(self):
                    super().__init__()
                    self.dwLength = ctypes.sizeof(self)
            st = MEMORYSTATUSEX()
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullAvailPhys / (1024 ** 3)
    except Exception:
        pass
    return total_ram_gb()


def nvidia_vram_gb():
    """VRAM видеокарты NVIDIA, ГБ; 0, если карты нет или команда не найдена."""
    for cmd in (["nvidia-smi",
                 "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                ["nvidia-smi", "-q"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if out.returncode != 0:
                continue
            text = out.stdout.strip()
            if cmd[1].startswith("--query"):
                for line in text.splitlines():
                    line = line.strip()
                    if line.isdigit() and int(line) > 0:
                        return int(line) / 1024
            else:
                for line in text.splitlines():
                    if "Total" in line and "MiB" in line:
                        digits = "".join(ch for ch in line if ch.isdigit())
                        if digits and int(digits) > 0:
                            return int(digits) / 1024
        except Exception:
            continue
    return 0.0


def cpu_cores() -> int:
    return max(1, os.cpu_count() or 1)


def detect() -> dict:
    ram_total = total_ram_gb()
    ram_free = available_ram_gb()
    vram = nvidia_vram_gb()
    # Сколько памяти реально можно отдать модели сейчас
    budget = max(ram_free * 0.8, vram) if vram else ram_free * 0.7
    return {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cpu_cores": cpu_cores(),
        "ram_total_gb": round(ram_total, 1),
        "ram_free_gb": round(ram_free, 1),
        "vram_gb": round(vram, 1),
        "budget_gb": round(budget, 1),
    }


def choose_models(hw: dict = None):
    """Возвращает (llm, embed, профиль, пояснение)."""
    hw = hw or detect()
    budget = hw["budget_gb"]
    vram = hw["vram_gb"]

    if vram >= 9:
        return (config.MODELS_MEDIUM[0], config.EMBED_MEDIUM[0], "gpu",
                f"видеокарта {hw['vram_gb']} ГБ — можно 7b")
    if budget >= 11:
        return (config.MODELS_MEDIUM[0], config.EMBED_MEDIUM[0], "ram",
                f"свободно {hw['ram_free_gb']} ГБ ОЗУ — 7b на процессоре")
    if budget >= 5.5:
        return (config.MODELS_SMALL[0], config.EMBED_TINY[0], "mid",
                f"свободно {hw['ram_free_gb']} ГБ — 3b, ответ будет медленнее")
    return (config.MODELS_TINY[0], config.EMBED_TINY[0], "tiny",
            f"свободно {hw['ram_free_gb']} ГБ — только 1.5b, "
            f"качество ответов будет скромным")


def save_models(llm: str, emb: str, profile: str, hw: dict):
    from . import db
    conn = db.connect()
    db.init_db(conn)
    db.set_meta(conn, "llm_model", llm)
    db.set_meta(conn, "embed_model", emb)
    db.set_meta(conn, "profile", profile)
    db.set_meta(conn, "hardware", json.dumps(hw, ensure_ascii=False))
    conn.close()


def load_models():
    from . import db
    try:
        conn = db.connect()
        llm = db.get_meta(conn, "llm_model")
        emb = db.get_meta(conn, "embed_model")
        profile = db.get_meta(conn, "profile")
        hw_raw = db.get_meta(conn, "hardware")
        conn.close()
        return (llm, emb, profile,
                json.loads(hw_raw) if hw_raw else None)
    except Exception:
        return (config.MODELS_TINY[0], config.EMBED_TINY[0], "tiny", None)


def main():
    hw = detect()
    llm, emb, profile, why = choose_models(hw)
    save_models(llm, emb, profile, hw)

    print("Компьютер:")
    print(f"  система            {hw['os']}")
    print(f"  ядер процессора    {hw['cpu_cores']}")
    print(f"  всего ОЗУ          {hw['ram_total_gb']} ГБ")
    print(f"  свободно ОЗУ       {hw['ram_free_gb']} ГБ")
    print(f"  видеокарта NVIDIA  {hw['vram_gb'] if hw['vram_gb'] else 'нет'}"
          + (f" ({hw['vram_gb']} ГБ)" if hw["vram_gb"] else ""))
    print("Выбор моделей:")
    print(f"  профиль            {profile} ({why})")
    print(f"  языковая модель    {llm}")
    print(f"  модель эмбеддингов {emb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())