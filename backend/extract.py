# -*- coding: utf-8 -*-
"""
Загрузка документов: PDF, DOCX, XLSX, MD, TXT, HTML -> плоский текст.

Тонкость с Excel: таблицу нельзя сваливать в текст построчно —
тогда «доля выполнения плана 87%» разрывается на «доля»,
«выполнения», «плана», «87%» и поиск по фразе перестаёт работать.
Поэтому XLSX превращается в markdown-таблицу: строка ячейки
остаётся строкой, и эмбеддинг видит её целиком.
"""
from pathlib import Path

# Расширения, из которых умеем читать
_PDF = {".pdf"}
_DOCX = {".docx"}
_XLSX = {".xlsx", ".xls"}
_MD = {".md", ".txt"}
_HTML = {".html", ".htm"}


def _require(modname, hint):
    try:
        return __import__(modname)
    except ImportError as exc:
        raise RuntimeError(
            f"Не установлен модуль {modname} — {hint}. "
            f"Запустите install.bat или: pip install {modname}"
        ) from exc


def read_pdf(path: Path) -> str:
    fitz = _require("fitz", "нужен для чтения PDF")
    parts = []
    with fitz.open(str(path)) as doc:
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            text = text.strip()
            if text:
                parts.append(f"\n\n[стр. {page_no}]\n{text}")
    return "".join(parts)


def read_docx(path: Path) -> str:
    docx = _require("docx", "нужен для чтения DOCX")
    doc = docx.Document(str(path))
    parts = []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue
        style = (para.style.name or "") if para.style else ""
        if style.startswith("Heading") or style == "Заголовок":
            parts.append(f"\n\n# {text}")
        else:
            parts.append(text)
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [(c.text or "").strip().replace("|", "/") for c in row.cells]
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            parts.append("\n\n| " + " | ".join(["---"] * len(
                [c for c in rows[0].split("|") if c.strip()])) + " |\n" + "\n".join(rows))
    return "\n\n".join(parts).strip()


def read_xlsx(path: Path) -> str:
    openpyxl = _require("openpyxl", "нужен для чтения XLSX")
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    try:
        for sheet in wb.worksheets:
            rows = []
            for r, row in enumerate(sheet.iter_rows(values_only=True)):
                if r > 2000:          # предохранитель от выгрузок на 100k строк
                    break
                cells = ["" if c is None else str(c).strip().replace("|", "/")
                         for c in row]
                if not any(cells):
                    continue
                rows.append("| " + " | ".join(cells) + " |")
            if not rows:
                continue
            ncols = rows[0].count("|") - 1
            header = "| " + " | ".join(["---"] * max(ncols, 1)) + " |"
            title = f"\n\n## Лист «{sheet.title}»\n"
            if len(rows) > 300:
                note = f"\n\n_Показаны первые 300 строк из {len(rows)}._"
                parts.append(title + header + "\n" + "\n".join(rows[:300]) + note)
            else:
                parts.append(title + header + "\n" + "\n".join(rows))
    finally:
        wb.close()
    return "\n\n".join(parts).strip()


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1251", "koi8-r"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_html(path: Path) -> str:
    import re
    html = read_text(path)
    html = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    html = re.sub(r"(?i)</t[dh]>", " | ", html)
    html = re.sub(r"<[^>]+>", " ", html)
    import html as html_mod
    text = html_mod.unescape(html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_text(path):
    """Возвращает текст документа или None, если формат не поддержан."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in _PDF:
        return read_pdf(path)
    if ext in _DOCX:
        return read_docx(path)
    if ext in _XLSX:
        return read_xlsx(path)
    if ext in _MD:
        return read_text(path)
    if ext in _HTML:
        return read_html(path)
    return None


def doc_type(path) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".pdf": "документ", ".docx": "документ", ".doc": "документ",
        ".xlsx": "таблица", ".xls": "таблица",
        ".md": "текст", ".txt": "текст", ".html": "страница", ".htm": "страница",
    }.get(ext, "документ")


def title_from(path: Path, text: str) -> str:
    """Заголовок документа: первая непустая строка, если она короткая.

    У полжений и протоколов первая строка обычно и есть название,
    но если это «СОДЕРЖАНИЕ» или длинный абзац — берём имя файла,
    иначе в списке источников будет 40 одинаковых «1. Общие положения».
    """
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if not s:
            continue
        if len(s) <= 140 and not s.isdigit():
            return s
        break
    return Path(path).stem