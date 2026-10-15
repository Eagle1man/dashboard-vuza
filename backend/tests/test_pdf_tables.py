# -*- coding: utf-8 -*-
"""
Проверка чтения PDF: таблицы и графические страницы.

Демонстрационный корпус сделан в markdown, поэтому путь с PDF здесь
не проверялся. Чтобы не отдавать код, который ни разу не отработал,
собираем настоящий PDF прямо здесь: страница с таблицей и страница
с одной картинкой без текста.

Запуск: python -m backend.tests.test_pdf_tables
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import extract  # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ОК     {name}")
    else:
        print(f"  ПРОВАЛ {name}  {detail}")
        FAILURES.append(name)


def build_pdf(path: Path):
    """Две страницы: первая с таблицей, вторая с картинкой без текста."""
    import pymupdf as fitz

    doc = fitz.open()

    # --- страница 1: заголовок, текст и таблица из линий ---
    page = doc.new_page()
    page.insert_text((60, 70), "Выручка и расходы университета за 2025 год",
                     fontsize=14)
    page.insert_text((60, 100), "Таблица 1. Финансовые показатели, млн рублей",
                     fontsize=10)

    # Метки латиницей: шрифт по умолчанию в PyMuPDF не содержит
    # кириллицы, и русские буквы при извлечении превращаются в точки.
    # Проверка должна тестировать извлечение таблиц, а не шрифт.
    rows = [("Metric", "2024", "2025"),
            ("Revenue", "3590", "3712"),
            ("Expenses", "3480", "3644"),
            ("Result", "110", "68")]
    x0, y0, w, h = 60, 120, 120, 22
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = fitz.Rect(x0 + c * w, y0 + r * h, x0 + (c + 1) * w,
                             y0 + (r + 1) * h)
            page.draw_rect(rect, color=(0.3, 0.3, 0.4), width=0.8)
            page.insert_text((rect.x0 + 5, rect.y0 + 15), cell, fontsize=9)

    # --- страница 2: только картинка, без текстового слоя ---
    page2 = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 120))
    pix.set_rect(pix.irect, (200, 200, 220))
    page2.insert_image(fitz.Rect(80, 150, 480, 460), pixmap=pix)

    doc.save(str(path))
    doc.close()


def main():
    tmp = Path(tempfile.gettempdir()) / "rag_pdf_check.pdf"
    print("Проверка чтения PDF: таблицы и графические страницы")
    print("  временный файл:", tmp)
    try:
        build_pdf(tmp)
    except Exception as exc:
        print("  ПРОВАЛ: не собрать тестовый PDF:", exc)
        return 1

    text, pages = extract.read_pdf(tmp)

    check("документ прочитан", bool(text) and len(text) > 50,
          f"{len(text)} символов")
    check("страниц две", len(pages) == 2, str([p["page_no"] for p in pages]))

    p1 = next((p for p in pages if p["page_no"] == 1), {})
    p2 = next((p for p in pages if p["page_no"] == 2), {})

    check("на странице с таблицей таблица найдена",
          p1.get("n_tables", 0) >= 1, f"n_tables={p1.get('n_tables')}")
    check("таблица попала в текст как markdown",
          "Revenue" in text and "|" in text, "в тексте нет markdown-таблицы")
    check("значение таблицы не потерялось", "3712" in text)

    check("страница с картинкой без текста опознана как графическая",
          p2.get("kind") == "image", f"kind={p2.get('kind')!r}")
    check("на графической странице в текст внесена пометка",
          "НЕ извлечено" in text, "модель не увидит, что график не прочитан")
    check("число картинок на странице зафиксировано",
          p2.get("n_images", 0) >= 1, f"n_images={p2.get('n_images')}")

    # Повторное чтение обязано давать тот же результат: иначе
    # переиндексация «дрожала» бы и куски менялись без причины.
    text2, pages2 = extract.read_pdf(tmp)
    check("повторное чтение даёт тот же текст", text == text2)
    check("повторное чтение даёт те же страницы", pages == pages2)

    # Шаблон разбора страниц должен уметь продолжить его и вернуть текст
    t3, p3 = extract.extract_text_with_pages(tmp)
    check("extract_text_with_pages согласован с read_pdf",
          t3 == text and p3 == pages)

    try:
        tmp.unlink()
    except OSError:
        pass

    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
        return 1
    print("Все проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())