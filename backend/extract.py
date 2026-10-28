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
import re

# Расширения, из которых умеем читать
_PDF = {".pdf"}
_DOCX = {".docx"}
_XLSX = {".xlsx"}
_XLS = {".xls"}          # старый формат — читается через xlrd, не openpyxl
_MD = {".md", ".txt", ".csv"}
_HTML = {".html", ".htm"}


def _require(modname, hint):
    try:
        return __import__(modname)
    except ImportError as exc:
        raise RuntimeError(
            f"Не установлен модуль {modname} — {hint}. "
            f"Запустите install.bat или: pip install {modname}"
        ) from exc


def _table_to_markdown(table) -> str:
    """Таблица PyMuPDF -> markdown, с ручной сборкой как запасным путём."""
    try:
        md = table.to_markdown()
        if md and md.strip():
            return md.strip()
    except Exception:
        pass
    try:
        rows = table.extract()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    for row in rows:
        cells = ["" if c is None else str(c).strip().replace("|", "/") for c in row]
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
    if not lines:
        return ""
    header = "| " + " | ".join(["---"] * (lines[0].count("|") - 1)) + " |"
    return header + "\n" + "\n".join(lines)


# Порог «страница почти без текста». Точное значение неважно: важно,
# чтобы страница с одним заголовком и схемой не считалась пустой,
# а страница с половиной текста — считалась графической.
GRAPHIC_TEXT_LIMIT = 120


def read_pdf(path: Path):
    """Возвращает (текст, список страниц).

    Про графические страницы. Если молча пропускать страницу, где нет
    извлекаемого текста, ассистент потом уверенно скажет «в документе
    про это ничего нет» — хотя на странице схема. Поэтому такая
    страница получает явную пометку в тексте: её видит модель и может
    сказать «здесь график, прочитать не могу».

    Таблицы из PDF достаются и кладутся в текст как markdown — так же,
    как это уже делается для XLSX.
    """
    fitz = _require("fitz", "нужен для чтения PDF")
    parts, pages = [], []

    with fitz.open(str(path)) as doc:
        for page_no, page in enumerate(doc, start=1):
            text = (page.get_text("text") or "").strip()

            try:
                n_images = len(page.get_images(full=True))
            except Exception:
                n_images = 0

            tables_md = []
            try:
                found = page.find_tables()
                for t in (found.tables if found else []):
                    md = _table_to_markdown(t)
                    if md:
                        tables_md.append(md)
            except Exception:
                # Таблицы находятся не на всех файлах и не во всех
                # сборках: отсутствие не повод ронять весь документ.
                tables_md = []

            kind = "text"
            if tables_md and not text:
                kind = "table"
            if n_images and len(text) < GRAPHIC_TEXT_LIMIT:
                kind = "image"

            body = text
            for md in tables_md:
                body += ("\n\n[таблица со страницы " + str(page_no) + "]\n\n" + md)
            if kind == "image":
                body += (
                    "\n\n[ВНИМАНИЕ, страница " + str(page_no) + ": на странице "
                    "изображение, схема или график. Текстовый слой либо "
                    "отсутствует, либо слишком короткий, поэтому содержание "
                    "изображения НЕ извлечено. Не делай выводов о том, что "
                    "на нём изображено.]")

            pages.append({
                "page_no": page_no,
                "kind": kind,
                "n_chars": len(text),
                "n_images": n_images,
                "n_tables": len(tables_md),
            })
            parts.append("\n\n[стр. " + str(page_no) + "]\n" + body)

    return "".join(parts), pages


def read_pdf_text(path: Path) -> str:
    """Только текст PDF — для случаев, где страницы не нужны."""
    return read_pdf(path)[0]


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


def read_xls(path: Path) -> str:
    """Старый формат Excel (BIFF) — через xlrd.

    openpyxl читает только .xlsx, поэтому раньше .xls в списке
    поддерживаемых значил «файл примут и не смогут прочитать» —
    ошибка всплывала уже при индексации. Теперь формат читается,
    а если xlrd не установлен, _require даёт понятную инструкцию.
    """
    xlrd = _require("xlrd", "нужен для чтения XLS")
    book = xlrd.open_workbook(str(path))
    parts = []
    for sheet in book.sheets():
        rows = []
        for r in range(min(sheet.nrows, 2000)):
            cells = []
            for c in range(sheet.ncols):
                v = sheet.cell_value(r, c)
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                cells.append(str(v).strip().replace("|", "/"))
            if not any(cells):
                continue
            rows.append("| " + " | ".join(cells) + " |")
        if not rows:
            continue
        ncols = max(rows[0].count("|") - 1, 1)
        header = "| " + " | ".join(["---"] * ncols) + " |"
        parts.append(f"\n\n## Лист «{sheet.name}»\n\n{header}\n"
                     + "\n".join(rows[:300]))
        if len(rows) > 300:
            parts.append(f"\n_Показаны первые 300 строк из {len(rows)}._")
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
    return extract_text_with_pages(path)[0]


def extract_text_with_pages(path):
    """Возвращает (текст, список страниц).

    Для PDF разбор идёт постранично: у каждой страницы запоминается,
    есть ли на ней изображение и нашлась ли таблица. Эти сведения нужны,
    чтобы система могла сказать «здесь график, текст не извлечён», а не
    промолчать и выдать уверенный ответ по неполным данным.

    Для остальных форматов список страниц пустой, текст прежний.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext in _PDF:
        return read_pdf(path)
    if ext in _DOCX:
        return read_docx(path), []
    if ext in _XLS:
        return read_xls(path), []
    if ext in _XLSX:
        return read_xlsx(path), []
    if ext in _MD:
        return read_text(path), []
    if ext in _HTML:
        return read_html(path), []
    return None, []


def doc_type(path) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".pdf": "документ", ".docx": "документ", ".doc": "документ",
        ".xlsx": "таблица", ".xls": "таблица",
        ".md": "текст", ".txt": "текст", ".html": "страница", ".htm": "страница",
    }.get(ext, "документ")


# Строки, которые названием документа не являются. В официальных
# документах их много: подпись электронной подписью, полное наименование
# вуза, «г. Ростов-на-Дону», номер страницы. Раньше название бралось
# из первой строки, и в списке источников у отчёта о самообследовании
# стояло «ЧАСТНОЕ ОБРАЗОВАТЕЛЬНОЕ УЧРЕЖДЕНИЕ ВЫСШЕГО» — то есть
# половина названия без половины смысла.
TITLE_SKIP = (
    "документ подписан", "электронной подписью", "сертификат:",
    "владелец:", "действителен:", "приложение", "к приказу",
    "частное образовательное учреждение", "частного образовательного",
    "южный университет", "юу (иубип)", "г. ростов-на-", "ростов-на-дону",
    "содержание", "оглавление", "страница", "приложение 1",
)

# Слова, по которым узнаётся название документа.
TITLE_MARK = (
    "отчет", "отчёт", "план", "положение", "результат", "протокол",
    "сведения", "инструментарий", "организационная структура",
    "мероприятия", "об итогах", "аналитический", "стратегия",
    "порядок", "регламент", "информация", "мониторинг", "контроль",
    "договор", "соглашение", "справка", "выписка",
)

# Слова, которых достаточно для названия и в одиночку.
STRONG_MARK = ("отчет", "отчёт", "план", "положение", "протокол",
               "сведения", "результат", "стратегия", "регламент")


def _is_title_candidate(line: str) -> bool:
    low = line.lower()
    for marker in TITLE_MARK:
        if marker in low:
            # Одно слово без пробелов — обрывок, а не название. Но
            # «Положение» или «Протокол» отдельно — нормальное название.
            if len(line.split()) < 2 and len(line) < 14 and marker not in STRONG_MARK:
                continue
            return True
    # Заголовок в верхнем регистре — обычный признак названия.
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 4 and all(c.isupper() for c in letters):
        return True
    return False


def title_from(path: Path, text: str) -> str:
    """Заголовок документа.

    Порядок поиска: сначала название в самом тексте, потом — имя файла.
    Название из текста полезнее: в файле у нас «01_otchet_samoobsledovaniya_2026»,
    а документ называется «ОТЧЕТ О САМООБСЛЕДОВАНИИ … по состоянию на
    01.04.2026 года». Но первая строка текста — это часто реквизиты
    («ДОКУМЕНТ ПОДПИСАН ЭЛЕКТРОННОЙ ПОДПИСЬЮ»), поэтому строки с
    реквизитами отбрасываются, а название ищется среди оставшихся.
    """
    lines = []
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            lines.append(s)
        # Окно — 60 строк, а не 25: у положений первая страница бывает
        # страницей электронной подписи, и название стоит на второй.
        if len(lines) >= 60:
            break

    clean = [s for s in lines
             if not any(marker in s.lower() for marker in TITLE_SKIP)
             and not s.isdigit() and len(s) <= 200]

    # Название может идти несколькими строками («ОБ ИТОГАХ УСПЕВАЕМОСТИ»
    # / «ОБУЧАЮЩИХСЯ В I СЕМЕСТРЕ 2025-2026 …»). Склеиваем до четырёх
    # коротких строк подряд, если они в верхнем регистре.
    for i, s in enumerate(clean):
        if not _is_title_candidate(s):
            continue
        parts = [s]
        j = i + 1
        while j < len(clean) and len(parts) < 4:
            nxt = clean[j]
            letters = [c for c in nxt if c.isalpha()]
            if not letters or not all(c.isupper() for c in letters):
                break
            if len(nxt) > 90:
                break
            parts.append(nxt)
            j += 1
        title = " ".join(parts)
        if len(title) > 140:
            title = title[:140].rsplit(" ", 1)[0]
        return title

    # Запасной путь — имя файла. Номер в начале убираем: «03_Положение
    # о внутренней оценке качества» читается как название, а
    # «03_Положение о внутренней оценке качества.pdf» — как имя файла.
    stem = re.sub(r"^\d+[_-]\s*", "", Path(path).stem)
    return stem.replace("_", " ").strip() or Path(path).stem