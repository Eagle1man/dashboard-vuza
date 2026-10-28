# -*- coding: utf-8 -*-
"""
Приём файла от подразделения (ТЗ 2.0).

Поток:
1. Анкета обязательна — проверяется на сервере, не только на форме.
2. Файл читается потоком: размер считается по ходу чтения, sha256 — тоже.
3. Формат сверяется по сигнатуре с заявленным расширением; для DOCX/XLSX
   дополнительно проверяется состав архива и защищается от «бомб».
4. Дубликат по содержимому: своему подразделению возвращается существующий
   upload_id и признак duplicate=true; чужому — 409 без сведений о чужой
   загрузке (ТЗ §6).
5. Имя файла на сервере — сгенерированное; исходное имя хранится в
   upload.original_name и в ответ не светится вместе с путями сервера.
6. Текст извлекается один раз: он идёт и в индекс, и в upload_raw.
7. Карточка разбора: анкета + небольшая модель (role=small) с одним
   повтором при некорректном JSON; при недоступной модели карточка
   строится из анкеты, а проверка это честно помечает.
8. Детерминированные проверки → upload_check (backend/checks.py).
9. Публикации здесь нет: числа уходят в витрину только после решения
   человека на экране проверки (backend/review.py).
"""
import csv
import hashlib
import io
import json
import os
import re
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from . import checks
from . import config
from . import db
from . import extract
from . import ingest as ingest_mod
from . import journal
from . import llm
from . import numeric

RAW_TEXT_CAP = 6000      # символов текста на строку сырья
PROMPT_BUDGET = 9000     # символов сырья уходит модели
PROMPT_MAX_ROWS = 160    # строк сырья уходит модели

# Типы документов — кандидаты для предположения карточки. Справочник
# типов и его редактирование — решение заказчика (ТЗ §19), здесь только
# разумный набор по умолчанию, он ничего не запрещает.
DOC_TYPES = [
    "отчёт о самообследовании", "план ФХД", "финансовый отчёт",
    "бухгалтерская отчётность", "протокол заседания", "приказ",
    "справка", "штатное расписание", "учебный план",
    "таблица показателей", "иной документ",
]

PARSE_SYSTEM = (
    "Ты разбираешь документы университета. Отвечай ТОЛЬКО валидным JSON, "
    "без markdown, без пояснений. Схема ответа: "
    '{"doc_type": "один из списка типов или свой вариант", '
    '"period": "период вида 2026-03 или как написано в документе", '
    '"values": [{"label": "подпись числа", "value": "1234.5", '
    '"metric_id": "только id из списка", "raw_ref": 123}]}. '
    "value — только число, без единиц измерения и без пробелов-разделителей. "
    "raw_ref — номер строки сырья, где найдено значение (0, если неизвестен). "
    "Не выдумывай чисел: берёшь только то, что явно написано в тексте."
)


# --------------------------------------------------------------------
# Формат
# --------------------------------------------------------------------
def _looks_text(head: bytes) -> bool:
    """Похож ли кусок файла на текст (для .txt/.md/.csv/.html)."""
    for enc in ("utf-8", "utf-8-sig"):
        try:
            s = head.decode(enc)
        except UnicodeDecodeError:
            continue
        ctrl = sum(1 for ch in s if ord(ch) < 32 and ch not in "\t\n\r")
        return (ctrl / max(len(s), 1)) < 0.02
    # cp1251 декодирует почти любые байты, поэтому фильтр по управляющим
    # символам здесь — единственный способ не принять бинарный мусор.
    try:
        s = head.decode("cp1251")
    except UnicodeDecodeError:
        return False
    ctrl = sum(1 for ch in s if ord(ch) < 32 and ch not in "\t\n\r")
    return (ctrl / max(len(s), 1)) < 0.02


def sniff(head: bytes) -> str:
    """Тип содержимого по сигнатуре: pdf | zip | ole | rtf | html | text | binary."""
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return "ole"
    if head[:5].lower() == b"{\\rtf":
        return "rtf"
    low = head[:2048].lower()
    if b"<html" in low or b"<!doctype html" in low:
        return "html"
    return "text" if _looks_text(head) else "binary"


_KIND_NAME = {
    "pdf": "PDF-документ", "zip": "ZIP-архив",
    "ole": "файл старого формата Office", "rtf": "RTF-документ",
    "html": "HTML-страница", "text": "текстовый файл",
    "binary": "бинарный файл",
}


def _zip_kind(path: Path) -> str:
    """Что внутри ZIP: docx | xlsx | pptx | None (+ защита от бомбы)."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > 5000:
                raise HTTPException(
                    413, detail="Архив содержит слишком много файлов")
            total = sum(i.file_size for i in infos)
            limit = 400 * 1024 * 1024
            if total > limit:
                raise HTTPException(
                    413, detail=f"Архив разворачивается в {total // (1 << 20)} "
                                f"МБ — больше лимита {limit // (1 << 20)} МБ")
            names = [i.filename for i in infos]
    except zipfile.BadZipFile:
        return None
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    if any(n.startswith("ppt/") for n in names):
        return "pptx"
    return None


def check_format(path: Path, head: bytes, declared: str) -> str:
    """Сверяет заявленное расширение с содержимым. Возвращает канонический ext.

    Отклонения — с понятной причиной: «заявлен PDF, а это таблица»,
    «старый формат .doc, пересохраните как .docx». Пути сервера в
    сообщения не попадают (ТЗ §11).
    """
    kind = sniff(head)
    d = (declared or "").lower()

    if d not in config.SUPPORTED_EXT:
        raise HTTPException(
            400, detail=f"Тип файла «{d or 'без расширения'}» не поддерживается."
                        f" Разрешено: {', '.join(sorted(config.SUPPORTED_EXT))}")

    TEXTY = {".txt", ".md", ".csv", ".html", ".htm"}

    if d == ".doc":
        raise HTTPException(
            400, detail="Формат .doc (Word 97) не читается — откройте документ "
                        "и сохраните его как .docx, затем загрузите заново")

    if d in TEXTY:
        if kind in ("pdf", "zip", "ole", "rtf"):
            raise HTTPException(
                415, detail=f"Файл заявлен как {d}, а по содержимому это "
                            f"{_KIND_NAME[kind]} — проверьте расширение")
        if kind == "binary":
            raise HTTPException(
                415, detail=f"Файл заявлен как {d}, но содержимое не похоже "
                            "на текстовый файл")
        return d

    if d == ".pdf":
        if kind != "pdf":
            raise HTTPException(
                415, detail=f"Файл заявлен как .pdf, а по содержимому это "
                            f"{_KIND_NAME.get(kind, kind)}")
        return d

    if d in (".docx", ".xlsx"):
        if kind == "ole":
            old = ".doc" if d == ".docx" else ".xls"
            raise HTTPException(
                415, detail=f"Файл заявлен как {d}, а это старый формат {old} — "
                            f"пересохраните документ в {d}")
        if kind != "zip":
            raise HTTPException(
                415, detail=f"Файл заявлен как {d}, а по содержимому это "
                            f"{_KIND_NAME.get(kind, kind)}")
        inner = _zip_kind(path)
        if inner == "pptx":
            raise HTTPException(415, detail="Файл PowerPoint не поддерживается")
        want = d.lstrip(".")
        if inner != want:
            raise HTTPException(
                415, detail=f"Файл заявлен как {d}, а внутри документ другого "
                            f"формата ({inner or 'не определён'})")
        return d

    if d == ".xls":
        if kind == "zip" and _zip_kind(path) == "xlsx":
            raise HTTPException(
                415, detail="Файл заявлен как .xls, а это .xlsx — "
                            "менять расширение не нужно, загрузите как .xlsx")
        if kind != "ole":
            raise HTTPException(
                415, detail=f"Файл заявлен как .xls, а по содержимому это "
                            f"{_KIND_NAME.get(kind, kind)}")
        return d

    if kind == "binary":
        raise HTTPException(
            415, detail=f"Файл заявлен как {d}, содержимое не распознано")
    return d


# --------------------------------------------------------------------
# Сохранение
# --------------------------------------------------------------------
def _save_tmp(file: UploadFile, save_dir: Path) -> tuple[Path, str, int, bytes]:
    """Потоковое чтение в tmp-файл: sha256 и размер считаются на лету.

    Чтение целиком в память (как раньше) позволяло отправить файл
    сколь угодно большого размера — лимит проверялся уже после того,
    как файл целиком оказался в оперативной памяти.
    """
    tmp = save_dir / (".tmp-" + uuid.uuid4().hex)
    h = hashlib.sha256()
    size = 0
    head = b""
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    try:
        with tmp.open("wb") as out:
            while True:
                block = file.file.read(1 << 20)
                if not block:
                    break
                if not head:
                    head = block[:4096]
                size += len(block)
                if size > limit:
                    raise HTTPException(
                        413, detail=f"Файл больше {config.MAX_UPLOAD_MB} МБ")
                h.update(block)
                out.write(block)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if size == 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise HTTPException(400, detail="Файл пустой")
    return tmp, h.hexdigest(), size, head


# --------------------------------------------------------------------
# Сырьё (upload_raw)
# --------------------------------------------------------------------
def _numstr(v) -> str:
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    return str(v)


def _raw_pdf(text: str) -> list[dict]:
    """Страницы PDF из уже извлечённого текста (маркеры «[стр. N]»)."""
    parts = re.split(r"\[стр\. (\d+)\]", text)
    rows = []
    if len(parts) < 3:
        body = (text or "").strip()
        if body:
            rows.append({"kind": "page", "label": "весь документ",
                         "text": body[:RAW_TEXT_CAP]})
        return rows
    for page_no, body in zip(parts[1::2], parts[2::2]):
        b = (body or "").strip()
        if not b:
            continue
        rows.append({"kind": "page", "label": f"стр. {page_no}",
                     "page_no": int(page_no), "text": b[:RAW_TEXT_CAP]})
    return rows


def _raw_xlsx(path: Path) -> list[dict]:
    openpyxl = __import__("openpyxl")
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    rows: list[dict] = []
    try:
        for sheet in wb.worksheets:
            for r, row in enumerate(sheet.iter_rows(), start=1):
                if len(rows) >= config.MAX_RAW_ROWS:
                    break
                vals = [c.value for c in row]
                if not any(v is not None and str(v).strip() for v in vals):
                    continue
                nums = [(i + 1, c.coordinate, c.value)
                        for i, c in enumerate(row)
                        if isinstance(c.value, (int, float))
                        and not isinstance(c.value, bool)]
                if not nums:
                    continue
                joined = " | ".join("" if v is None else str(v)
                                    for v in vals)[:RAW_TEXT_CAP]
                rows.append({"kind": "row", "sheet": sheet.title, "row_no": r,
                             "label": f"{sheet.title}!строка {r}",
                             "text": joined})
                for col, coord, v in nums:
                    if len(rows) >= config.MAX_RAW_ROWS:
                        break
                    rows.append({"kind": "cell", "sheet": sheet.title,
                                 "row_no": r, "col_no": col, "cell_ref": coord,
                                 "label": f"{sheet.title}!{coord}",
                                 "text": _numstr(v)})
            if len(rows) >= config.MAX_RAW_ROWS:
                break
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return rows


def _raw_xls(path: Path) -> list[dict]:
    xlrd = __import__("xlrd")
    book = xlrd.open_workbook(str(path))
    rows: list[dict] = []
    for si, sheet in enumerate(book.sheets()):
        for r in range(sheet.nrows):
            if len(rows) >= config.MAX_RAW_ROWS:
                break
            vals = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
            if not any(str(v).strip() for v in vals):
                continue
            nums = [(c + 1, v) for c, v in enumerate(vals)
                    if isinstance(v, float)]
            if not nums:
                continue
            joined = " | ".join(_numstr(v) for v in vals)[:RAW_TEXT_CAP]
            rows.append({"kind": "row", "sheet": sheet.name, "row_no": r + 1,
                         "label": f"{sheet.name}!строка {r + 1}",
                         "text": joined})
            for col, v in nums:
                if len(rows) >= config.MAX_RAW_ROWS:
                    break
                rows.append({"kind": "cell", "sheet": sheet.name, "row_no": r + 1,
                             "col_no": col,
                             "cell_ref": f"{col_letter(col)}{r + 1}",
                             "label": f"{sheet.name}!{col_letter(col)}{r + 1}",
                             "text": _numstr(v)})
    return rows


def col_letter(n: int) -> str:
    """1 -> A, 27 -> AA (адреса ячеек для xls/csv, где openpyxl не нужен)."""
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def _raw_docx(path: Path) -> list[dict]:
    import docx as docx_mod
    doc = docx_mod.Document(str(path))
    rows: list[dict] = []
    for i, para in enumerate(doc.paragraphs, 1):
        if len(rows) >= config.MAX_RAW_ROWS:
            break
        t = (para.text or "").strip()
        if len(t) < 3:
            continue
        rows.append({"kind": "block", "label": f"абзац {i}",
                     "text": t[:RAW_TEXT_CAP]})
    for ti, table in enumerate(doc.tables, 1):
        for ri, row in enumerate(table.rows, 1):
            if len(rows) >= config.MAX_RAW_ROWS:
                break
            cells = [(c.text or "").strip() for c in row.cells]
            if not any(cells):
                continue
            nums = [ci for ci, c in enumerate(cells, 1)
                    if numeric.to_decimal(c) is not None]
            if not nums:
                continue
            rows.append({"kind": "row", "sheet": f"таблица {ti}", "row_no": ri,
                         "label": f"таблица {ti}!строка {ri}",
                         "text": " | ".join(cells)[:RAW_TEXT_CAP]})
            for ci in nums:
                if len(rows) >= config.MAX_RAW_ROWS:
                    break
                rows.append({"kind": "cell", "sheet": f"таблица {ti}",
                             "row_no": ri, "col_no": ci,
                             "cell_ref": f"T{ti}R{ri}C{ci}",
                             "label": f"таблица {ti}!строка {ri} кол.{ci}",
                             "text": cells[ci - 1]})
    return rows


def _raw_csv(path: Path) -> list[dict]:
    txt = extract.read_text(path)
    rows: list[dict] = []
    for r_i, cells in enumerate(csv.reader(io.StringIO(txt)), 1):
        if len(rows) >= config.MAX_RAW_ROWS:
            break
        if not cells or not any(c.strip() for c in cells):
            continue
        nums = [(ci, c) for ci, c in enumerate(cells, 1)
                if numeric.to_decimal(c) is not None]
        if not nums:
            continue
        rows.append({"kind": "row", "sheet": "csv", "row_no": r_i,
                     "label": f"строка {r_i}",
                     "text": " | ".join(cells)[:RAW_TEXT_CAP]})
        for ci, v in nums:
            if len(rows) >= config.MAX_RAW_ROWS:
                break
            rows.append({"kind": "cell", "sheet": "csv", "row_no": r_i,
                         "col_no": ci, "cell_ref": f"{col_letter(ci)}{r_i}",
                         "label": f"строка {r_i} кол.{ci}", "text": v})
    return rows


def _raw_text(text: str) -> list[dict]:
    rows, buf, size, n = [], [], 0, 0
    for line in (text or "").splitlines():
        buf.append(line)
        size += len(line) + 1
        if size >= 1400:
            n += 1
            rows.append({"kind": "block", "label": f"фрагмент {n}",
                         "text": "\n".join(buf)[:RAW_TEXT_CAP]})
            buf, size = [], 0
        if len(rows) >= config.MAX_RAW_ROWS:
            break
    if buf and len(rows) < config.MAX_RAW_ROWS:
        n += 1
        rows.append({"kind": "block", "label": f"фрагмент {n}",
                     "text": "\n".join(buf)[:RAW_TEXT_CAP]})
    return rows


def build_raw(path: Path, ext: str, text: str, pages: list) -> list[dict]:
    """Сырые строки файла. Любой сбой здесь не должен ронять приём."""
    try:
        if ext == ".pdf":
            rows = _raw_pdf(text)
        elif ext == ".xlsx":
            rows = _raw_xlsx(path)
        elif ext == ".xls":
            rows = _raw_xls(path)
        elif ext == ".docx":
            rows = _raw_docx(path)
        elif ext == ".csv":
            rows = _raw_csv(path)
        else:
            rows = _raw_text(text)
    except Exception:
        rows = _raw_text(text)
    if len(rows) > config.MAX_RAW_ROWS:
        rows = rows[:config.MAX_RAW_ROWS]
    return rows


def _insert_raw(conn, upload_id: int, rows: list[dict]) -> list[int]:
    ids = []
    for i, r in enumerate(rows):
        cur = conn.execute(
            "INSERT INTO upload_raw(upload_id, kind, label, text, page_no,"
            " sheet, row_no, col_no, cell_ref, seq) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (upload_id, r.get("kind"), r.get("label"), r.get("text"),
             r.get("page_no"), r.get("sheet"), r.get("row_no"),
             r.get("col_no"), r.get("cell_ref"), i))
        ids.append(int(cur.lastrowid))
    conn.commit()
    return ids


# --------------------------------------------------------------------
# Карточка разбора
# --------------------------------------------------------------------
def _catalog(conn) -> dict:
    cat = {}
    for r in conn.execute(
            "SELECT metric_id, title, unit FROM dim_metric ORDER BY ord"):
        cat[r["metric_id"]] = {"title": r["title"], "unit": r["unit"]}
    return cat


def _sample_rows(raw: list[dict]) -> list[dict]:
    out, used = [], 0
    # Сначала строки с числами и адресами — в них искомые значения,
    # потом остальное как контекст подписей.
    ordered = ([r for r in raw if r.get("kind") in ("row", "cell")]
               + [r for r in raw if r.get("kind") not in ("row", "cell")])
    for r in ordered:
        if len(out) >= PROMPT_MAX_ROWS or used >= PROMPT_BUDGET:
            break
        t = r.get("text") or ""
        if not t.strip():
            continue
        out.append(r)
        used += len(t) + 60
    out.sort(key=lambda r: r.get("seq", 0))
    return out


def _prompt_user(form: dict, cat: dict, raw: list[dict]) -> str:
    lines = [
        "Анкета загрузки:",
        f"  что это: {form.get('title', '')}",
        f"  зачем: {form.get('purpose', '')}",
        f"  период: {form.get('period', '')}",
        "",
        "Каталог показателей (id — единственный допустимый metric_id):",
    ]
    for mid, info in list(cat.items())[:60]:
        unit = f", {info['unit']}" if info.get("unit") else ""
        lines.append(f"  {mid}: {info['title']}{unit}")
    lines.append("")
    lines.append("Строки сырья (raw_id | метка | текст):")
    for r in raw:
        t = " ".join(str(r.get("text") or "").split())
        if len(t) > 400:
            t = t[:400] + "…"
        lines.append(f"  {r['raw_id']} | {r.get('label', '')} | {t}")
    lines.append("")
    lines.append('Ответ — только JSON со схемой из инструкции.')
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Достаёт первый объект JSON из ответа модели (иногда он в ```)."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    start = t.find("{")
    end = t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("в ответе нет JSON")
    return json.loads(t[start:end + 1])


def _validate_card(data: dict, cat: dict, valid_refs: set[int]) -> dict:
    """Строгая проверка ответа модели. Ошибка → понятная причина для ретрая."""
    if not isinstance(data, dict):
        raise ValueError("ответ не объект")
    doc_type = str(data.get("doc_type") or "").strip()[:120]
    period = str(data.get("period") or "").strip()[:80]
    values = data.get("values")
    if not isinstance(values, list):
        raise ValueError("нет массива values")
    out = []
    for v in values[:40]:
        if not isinstance(v, dict):
            continue
        label = str(v.get("label") or "").strip()[:200]
        raw_val = v.get("value")
        d = numeric.to_decimal(raw_val)
        if d is None:
            continue
        mid = str(v.get("metric_id") or "").strip()
        if mid and mid not in cat:
            mid = ""
        ref = v.get("raw_ref")
        try:
            ref = int(ref)
        except (TypeError, ValueError):
            ref = 0
        out.append({"label": label or "без подписи",
                    "value": numeric.exact(d),
                    "metricId": mid,
                    "rawRef": ref if ref in valid_refs else None})
    return {"docType": doc_type, "period": period, "values": out}


def build_card(conn, upload_id: int, form: dict,
               raw_with_ids: list[dict]) -> dict:
    """Строит карточку: анкета + модель small (один повтор при сбое JSON)."""
    cat = _catalog(conn)
    period = str(form.get("period") or "").strip()

    mapping: list[dict] = []
    try:
        metric_ids = json.loads(form.get("metric_ids") or "[]")
    except json.JSONDecodeError:
        metric_ids = []
    try:
        metric_values = json.loads(form.get("metric_values") or "{}")
    except json.JSONDecodeError:
        metric_values = {}
    if not isinstance(metric_ids, list):
        metric_ids = []
    if not isinstance(metric_values, dict):
        metric_values = {}

    for mid in metric_ids:
        info = cat.get(str(mid))
        if not info:
            continue
        val = metric_values.get(str(mid))
        mapping.append({
            "metricId": str(mid), "metricTitle": info["title"],
            "unit": info.get("unit"), "value": "" if val is None else str(val),
            "source": "questionnaire", "confidence": 1.0,
            "rawRef": None, "label": info["title"]})

    values: list[dict] = []
    unmapped: list[dict] = []
    parse_error = None
    attempts = 0
    parse_ms = 0
    model_desc = None
    doc_type_default = "документ"

    valid_refs = {r["raw_id"] for r in raw_with_ids}
    if raw_with_ids:
        user = _prompt_user(form, cat, _sample_rows(raw_with_ids))
        for attempt in (1, 2):
            attempts = attempt
            text, meta = llm.chat(PARSE_SYSTEM, user, role="small",
                                  purpose="parse_card", num_predict=900)
            model_desc = f"small:{meta.get('model')}"
            parse_ms += int(meta.get("duration_ms") or 0)
            if not meta.get("ok"):
                parse_error = meta.get("reason") or "модель недоступна"
                break
            try:
                card = _validate_card(_extract_json(text), cat, valid_refs)
                values = card["values"]
                if card["period"]:
                    period = card["period"]
                doc_type_default = card["docType"] or doc_type_default
                parse_error = None
                break
            except (ValueError, json.JSONDecodeError) as exc:
                parse_error = f"модель вернула некорректный JSON: {exc}"
                if attempt == 1:
                    user += ("\n\nПредыдущий ответ не прошёл проверку: "
                             f"{exc}. Верни только JSON.")

    # Слияние: анкета приоритетнее модели (число ввёл человек),
    # модель добавляет найденные в файле значения.
    have = {m["metricId"] for m in mapping}
    for v in values:
        mid = v.get("metricId")
        if mid and mid in have:
            # Сравнение с введённым человеком значением — в проверках.
            for m in mapping:
                if m["metricId"] == mid:
                    if v.get("value"):
                        m["modelValue"] = v["value"]
                        if not str(m.get("value") or "").strip():
                            m["value"] = v["value"]
                            m["source"] = "model"
                    if v.get("rawRef") and not m.get("rawRef"):
                        m["rawRef"] = v["rawRef"]
            continue
        if mid:
            info = cat.get(mid, {})
            mapping.append({
                "metricId": mid, "metricTitle": info.get("title", mid),
                "unit": info.get("unit"), "value": v.get("value") or "",
                "source": "model", "confidence": 0.6,
                "rawRef": v.get("rawRef"), "label": v.get("label") or mid})
            have.add(mid)
        else:
            unmapped.append({"label": v.get("label") or "без подписи",
                             "value": v.get("value"),
                             "rawRef": v.get("rawRef"),
                             "reason": "не сопоставлено с показателем"})

    card = {"docType": doc_type_default, "period": period,
            "periodId": None, "mapping": mapping, "values": values,
            "unmapped": unmapped, "status": "draft",
            "parseError": parse_error}

    conn.execute(
        "INSERT OR REPLACE INTO parse_card(upload_id, doc_type, division_id,"
        " period, period_id, values_json, mapping_json, unmapped_json, status,"
        " model, attempts, parse_ms, parse_error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (upload_id, card["docType"], form.get("_division_id"),
         card["period"], None,
         json.dumps(values, ensure_ascii=False),
         json.dumps(mapping, ensure_ascii=False),
         json.dumps(unmapped, ensure_ascii=False),
         "draft", model_desc, max(attempts, 1), parse_ms, parse_error))
    conn.execute("UPDATE upload SET parsed_at = ? WHERE upload_id = ?",
                 (time.strftime("%Y-%m-%d %H:%M:%S"), upload_id))
    conn.commit()
    return card


# --------------------------------------------------------------------
# Главный вход
# --------------------------------------------------------------------
def receive(division_id: str, login: str | None, file: UploadFile,
            form: dict) -> dict:
    """Весь конвейер приёма: сохранение → индексация → сырьё → карточка → проверки."""
    # 1. Анкета обязательна (сервер, не только форма).
    missing = [label for label, key in (
        ("что это за данные", "title"),
        ("зачем данные", "purpose"),
        ("период", "period"),
        ("ответственный", "responsible"),
    ) if not str(form.get(key, "")).strip()]
    if missing:
        raise HTTPException(
            400, detail="Анкета обязательна — заполните: " + ", ".join(missing))

    if not file.filename:
        raise HTTPException(400, detail="Файл не выбран")
    declared = Path(file.filename).suffix.lower()

    year = time.strftime("%Y")
    save_dir = config.KNOWLEDGE_DIR / division_id / year
    save_dir.mkdir(parents=True, exist_ok=True)
    original_name = Path(file.filename).name

    tmp, sha, size, head = _save_tmp(file, save_dir)

    # 2. Формат и дубликат — до записи на постоянное место.
    try:
        ext = check_format(tmp, head, declared)

        conn = db.connect()
        dup = conn.execute(
            "SELECT upload_id, division_id FROM upload WHERE sha256 = ?"
            " ORDER BY upload_id DESC LIMIT 1", (sha,)).fetchone()
        if dup:
            if dup["division_id"] != division_id:
                journal.log(conn, "upload_dup", actor=login, role=division_id,
                            detail="дубликат чужого подразделения")
                raise HTTPException(
                    409, detail="Такой файл уже загружен в систему")
            tmp.unlink(missing_ok=True)
            journal.log(conn, "upload_dup", actor=login, role=division_id,
                        entity=f"upload:{dup['upload_id']}",
                        detail=original_name)
            return {"duplicate": True,
                    "upload_id": int(dup["upload_id"]),
                    "status": "duplicate"}

        final_name = uuid.uuid4().hex + ext
        final = save_dir / final_name
        os.replace(tmp, final)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    rel_path = f"{division_id}/{year}/{final_name}"

    # 3. Текст один раз — он пойдёт и в индекс, и в сырьё.
    try:
        extracted = extract.extract_text_with_pages(final)
        text = extracted[0]
        if not text or not text.strip():
            raise HTTPException(
                400, detail="В файле не найдено текста для индексации")
        conn = db.connect()
        res = ingest_mod.index_file(conn, final, is_demo=False, force=True,
                                    extracted=extracted)
        if res["status"] in ("empty", "no-chunks"):
            raise HTTPException(
                400, detail="В файле не найдено текста для индексации")
    except HTTPException:
        try:
            final.unlink()
        except OSError:
            pass
        raise
    except Exception as exc:
        try:
            final.unlink()
        except OSError:
            pass
        # Клиенту — тип ошибки без путей; полная причина — в upload.error.
        raise HTTPException(
            500, detail="Не удалось обработать файл: "
                        f"{type(exc).__name__}") from exc

    doc_row = conn.execute("SELECT doc_id FROM doc WHERE rel_path = ?",
                           (rel_path,)).fetchone()
    doc_id = int(doc_row["doc_id"])

    # 4. Загрузка и анкета.
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    title = str(form.get("title") or original_name)
    purpose = str(form.get("purpose") or "")
    period = str(form.get("period") or now[:7])
    frequency = str(form.get("frequency") or "разово")
    responsible = str(form.get("responsible") or "")
    access_level = str(form.get("access_level") or "open")
    try:
        metric_ids = json.loads(form.get("metric_ids") or "[]")
    except json.JSONDecodeError:
        metric_ids = []
    next_exp = db.next_expected_for(frequency)

    cur = conn.execute(
        "INSERT INTO upload(division_id, doc_id, filename, rel_path, sha256,"
        " size_bytes, uploaded_at, status, original_name, mime_detected,"
        " extension, uploaded_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (division_id, doc_id, original_name, rel_path, sha, size, now,
         "needs_review", original_name, file.content_type, ext,
         login or division_id))
    upload_id = int(cur.lastrowid)

    form = dict(form)
    form["_division_id"] = division_id

    conn.execute(
        "INSERT OR REPLACE INTO doc_meta(doc_id, upload_id, division_id, title,"
        " purpose, period, frequency, responsible, access_level, next_expected,"
        " metric_ids, confirmed, uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, upload_id, division_id, title, purpose, period, frequency,
         responsible, access_level, next_exp,
         json.dumps(metric_ids, ensure_ascii=False), 0, now))
    conn.commit()

    # 5. Сырьё и карточка.
    rows = build_raw(final, ext, text, extracted[1])
    raw_ids = _insert_raw(conn, upload_id, rows)
    raw_with_ids = [dict(r, raw_id=rid, seq=i)
                    for i, (r, rid) in enumerate(zip(rows, raw_ids))]

    card = build_card(conn, upload_id, form, raw_with_ids)

    # 6. Проверки.
    findings = checks.run(conn, upload_id)
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = len(findings) - errors

    journal.log(conn, "upload", actor=login, role=division_id,
                entity=f"upload:{upload_id}",
                detail=f"{original_name}, {size} Б, ошибок {errors}")

    return {
        "upload_id": upload_id,
        "doc_id": doc_id,
        "rel_path": rel_path,
        "filename": original_name,
        "title": title,
        "chunks": res.get("chunks", 0),
        "blocks": res.get("blocks", []),
        "next_expected": next_exp,
        "confirmed": False,
        "duplicate": False,
        "status": "needs_review",
        "review_url": f"/review.html?id={upload_id}",
        "errors": errors,
        "warnings": warnings,
        "raw_rows": len(rows),
        "card": {"docType": card["docType"], "period": card["period"],
                 "mapping": len(card["mapping"]),
                 "unmapped": len(card["unmapped"]),
                 "parseError": card.get("parseError")},
    }
