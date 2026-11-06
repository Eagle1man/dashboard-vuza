# -*- coding: utf-8 -*-
"""Собирает Word-версию регламента из docs/РЕГЛАМЕНТ-ЗАГРУЗКИ.md.

Зачем отдельный скрипт, а не файл, правленный руками: два документа,
которые расходятся, — худший вид инструкции. Источник правды — Markdown
в репозитории, Word нужен только для печати и рассылки.

    python docs/tools/make_regulation_docx.py

Требует python-docx (он уже в requirements.txt бэкенда).
"""
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent.parent
MD = ROOT / "docs" / "РЕГЛАМЕНТ-ЗАГРУЗКИ.md"
DOCX = ROOT / "docs" / "РЕГЛАМЕНТ-ЗАГРУЗКИ.docx"

INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
CODE = re.compile(r"(`[^`]+`)")
SPECIAL = ("#", "|", "- ", "* ", "```")


def add_inline(paragraph, text, bold=False):
    """**жирный** и `код` внутри абзаца.

    Обратные кавычки — разметка Markdown, в Word они не нужны: код
    рисуем моноширинным, звёздочки жирного убираем. Жирный может
    содержать код внутри (```**Word (`.docx`) — ...**```), поэтому
    содержимое жирного разбирается ещё раз.
    """
    for token in INLINE.split(text):
        if not token:
            continue
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            add_inline(paragraph, token[2:-2], bold=True)
        elif token.startswith("`") and token.endswith("`"):
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
            run.bold = bold
        else:
            run = paragraph.add_run(token)
            run.bold = bold


def is_table_row(line):
    return line.strip().startswith("|") and line.strip().endswith("|")


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_special(line):
    s = line.strip()
    return (not s) or s == "---" or s.startswith(SPECIAL)


def blocks(lines):
    """Markdown -> блоки. Абзац в Markdown может занимать несколько
    строк, и перенос строки не означает новый абзац: без склейки Word
    рвал предложения на куски."""
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or s == "---":
            i += 1
            continue

        if s.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            yield ("code", code)
            continue

        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            yield ("h", level, s[level:].strip())
            i += 1
            continue

        if is_table_row(s):
            rows = []
            while i < len(lines) and is_table_row(lines[i].strip()):
                rows.append(split_row(lines[i]))
                i += 1
            rows = [r for r in rows
                    if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
            if rows:
                yield ("table", rows)
            continue

        if s.startswith(("- ", "* ")):
            item = s[2:]
            i += 1
            while i < len(lines) and not is_special(lines[i]):
                item += " " + lines[i].strip()
                i += 1
            yield ("li", item)
            continue

        para = s
        i += 1
        while i < len(lines) and not is_special(lines[i]):
            para += " " + lines[i].strip()
            i += 1
        yield ("p", para)


def build():
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.15

    for section in doc.sections:
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(1.5)
        section.top_margin = Cm(1.8)
        section.bottom_margin = Cm(1.8)

    for name in ("Heading 1", "Heading 2", "Heading 3"):
        st = doc.styles[name]
        st.font.name = "Times New Roman"
        st.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)

    lines = MD.read_text(encoding="utf-8").splitlines()
    for block in blocks(lines):
        kind = block[0]

        if kind == "h":
            _, level, text = block
            heading = doc.add_heading(level=min(level, 3))
            add_inline(heading, text)
            if level == 1:
                heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
            continue

        if kind == "code":
            for row in block[1]:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(0)
                run = p.add_run(row)
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)
            doc.add_paragraph()
            continue

        if kind == "table":
            rows = block[1]
            table = doc.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Table Grid"
            for r_idx, row in enumerate(rows):
                for c_idx, value in enumerate(row[:len(rows[0])]):
                    cell = table.cell(r_idx, c_idx)
                    cell.text = ""
                    p = cell.paragraphs[0]
                    p.paragraph_format.space_after = Pt(0)
                    add_inline(p, value)
                    for run in p.runs:
                        run.font.size = Pt(9.5)
                        if r_idx == 0:
                            run.bold = True
            doc.add_paragraph()
            continue

        if kind == "li":
            add_inline(doc.add_paragraph(style="List Bullet"), block[1])
            continue

        add_inline(doc.add_paragraph(), block[1])

    doc.save(DOCX)
    return DOCX


if __name__ == "__main__":
    if not MD.exists():
        print("нет файла:", MD)
        sys.exit(1)
    out = build()
    print(f"готово: {out} ({out.stat().st_size // 1024} КБ)")
