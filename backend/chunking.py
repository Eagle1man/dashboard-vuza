# -*- coding: utf-8 -*-
"""
Нарезка текста на куски для RAG.

Два решения, которые отличают рабочую нарезку от наивной:

1. Режем ПО ЗАГОЛОВКАМ, а не по длине. В положениях и протоколах
   пункты нумеруются («1. Общие положения», «2. Порядок зачисления»).
   Если разрезать такой документ пополам, вопрос «как проходит
   аттестация» попадёт в кусок, где половина пунктов, и модель
   ответит наугад. Куски не пересекают границы пунктов.

2. Оценка длины в токенах, а не в символах. У русского текста
   примерно 2 символа на токен, но у текста с таблицами и
   номерами пунктов соотношение скачет. Точный подсчёт требует
   токенизатора модели, а он может смениться вместе с моделью,
   поэтому здесь приближение с запасом: режем так, чтобы кусок
   гарантированно влез в контекст.
"""
import re

from . import config

# Приближение «символов на токен». Для русского текста без таблиц
# отношение около 2.2; с таблицами падает до 1.6.
CHARS_PER_TOKEN = 1.8

# Заголовок раздела. Важная деталь: пункт вида «1.1. Настоящее Положение
# определяет порядок приема на обучение по программам бакалавриата…»
# под это выражение попадает по форме, но заголовком он не является.
# Поэтому у номерованных пунктов ограничение по длине: короткий
# «2. Кворты приёма» — заголовок, длинный абзац с номером — обычный текст.
# Иначе в графе «раздел» источника появляется целое предложение,
# а раздел без единого пункта под ним даёт кусок из одной строки.
HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:#{1,6}\s+.+)"                                # markdown # Заголовок
    r"|(?:\d+(?:\.\d+)*[.)]\s+\S.{0,70}\S)\s*$"       # 2. Кворты приёма
    r"|(?:Раздел|Глава|Статья|Пункт|Приложение)\s+[IVXLC\d]+.{0,70}"
    r"|(?:[А-ЯЁ][А-ЯЁ\s,.\-]{6,70})"                  # ПРАВИЛА ПРИЁМА
    r")\s*$"
)


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def estimate_chars(tokens: int) -> int:
    return int(tokens * CHARS_PER_TOKEN)


def _split_sections(text: str):
    """Делит текст на разделы по заголовкам.

    Возвращает список (заголовок, текст). Строка заголовка в текст
    раздела НЕ включается: она хранится отдельно и попадает в
    полнотекстовый индекс отдельной колонкой. Если включить её
    в тело, раздел без пунктов даст кусок, состоящий из одной
    строки-заголовка, — такие куски бесполезны для поиска и,
    к тому же, одинаковы в разных документах.
    """
    lines = text.splitlines()
    sections = []
    cur_head = ""
    cur_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and HEADING_RE.match(stripped):
            body = "\n".join(cur_lines).strip()
            if body:
                sections.append((cur_head, body))
            cur_head = stripped.lstrip("#").strip()
            cur_lines = []
        else:
            cur_lines.append(line)

    body = "\n".join(cur_lines).strip()
    if body:
        sections.append((cur_head, body))
    return sections


def _tail_overlap(text: str, overlap_chars: int) -> str:
    """Последние overlap_chars символов — для перекрытия между кусками."""
    if overlap_chars <= 0 or len(text) <= overlap_chars:
        return text
    return text[-overlap_chars:]


def chunk_text(text: str, chunk_tokens: int = None, overlap: int = None,
               max_chars: int = 1600):
    """Режет текст на куски.

    Возвращает список словарей: {heading, text}.
    Разделы короче цели помещаются в один кусок и не дробятся,
    даже если это выгоднее для вектора: разорванный пункт хуже,
    чем чуть более длинный кусок.
    """
    chunk_tokens = chunk_tokens or config.CHUNK_TOKENS
    overlap = config.CHUNK_OVERLAP if overlap is None else overlap
    limit = estimate_chars(chunk_tokens)
    overlap_chars = estimate_chars(overlap)

    out = []
    for head, body in _split_sections(text):
        if not body:
            continue
        if len(body) <= limit:
            out.append({"heading": head, "text": body})
            continue
        # Раздел длиннее куска — режем по абзацам, но не разрывая их.
        para_chunks = []
        cur = []
        cur_len = 0
        for para in body.split("\n\n"):
            plen = len(para) + 2
            if cur and cur_len + plen > limit:
                para_chunks.append("\n\n".join(cur))
                cur = [_tail_overlap("\n\n".join(cur), overlap_chars)]
                cur_len = len(cur[0])
            # Абзац длиннее лимита (таблица, большой список) — режем строками
            if plen > limit:
                if cur:
                    para_chunks.append("\n\n".join(cur))
                    cur, cur_len = [], 0
                buf, blen = [], 0
                for line in para.splitlines():
                    ll = len(line) + 1
                    if blen + ll > limit and buf:
                        para_chunks.append("\n".join(buf))
                        buf = [_tail_overlap("\n".join(buf), overlap_chars)]
                        blen = len(buf[0])
                    buf.append(line)
                    blen += ll
                if buf:
                    para_chunks.append("\n".join(buf))
                continue
            cur.append(para)
            cur_len += plen
        if cur:
            tail = "\n\n".join(cur).strip()
            if tail:
                para_chunks.append(tail)

        for pc in para_chunks:
            pc = pc.strip()
            if not pc:
                continue
            if max_chars and len(pc) > max_chars:
                # последний предохранитель: контекст модели не примет больше
                pc = pc[:max_chars].rsplit(" ", 1)[0]
            out.append({"heading": head, "text": pc})

    return out


def chunk_document(text: str, **kwargs):
    """То же, но возвращает куски с номерами и хэшем — форма для БД."""
    import hashlib
    chunks = []
    for i, ch in enumerate(chunk_text(text, **kwargs)):
        h = hashlib.sha256(
            (ch["heading"] + "\x1e" + ch["text"]).encode("utf-8")
        ).hexdigest()
        chunks.append({
            "ord": i,
            "heading": ch["heading"],
            "text": ch["text"],
            "n_chars": len(ch["text"]),
            "chunk_hash": h,
        })
    return chunks