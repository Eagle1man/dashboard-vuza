# -*- coding: utf-8 -*-
"""
Граф связей между документами.

Почему граф, а не «просто ещё один поиск». В корпусе вуза документы
связаны явно и по делу: положение утверждено приказом, протокол
ссылается на положение, отчёт ссылается на протокол. Вопрос «какое
положение утвердило то, что протокол № 14 изменил по успеваемости»
нельзя решить поиском по похожим словам — нужных слов в вопросе
может не быть вовсе. Нужен переход по связям.

Почему не полноценный граф знаний с извлечением сущностей моделью.
Такое извлечение на 1.5b без видеокарты даёт мусор и занимает минуты
на документ: при сотне документов индексация растянется на часы, а
система станет работать хуже нынешней. Здесь связи детерминированные
и берутся регулярками: номера приказов и протоколов, упоминания,
заголовки разделов. Секунды вместо часов и никакой зависимости от
качества модели.

Сущности:
  doc      — документ
  section  — раздел документа (по заголовку)
  order    — приказ (№ 214-р)
  protocol — протокол (№ 14)

Связи:
  doc --contains--> section
  section --mentions--> order | protocol
"""
import re
import time

from . import config
from . import db

# --- Шаблоны распознавания ссылок -----------------------------------
# Порядок важен: сначала самые длинные формулировки, иначе «приказ
# ректора от 28 октября 2025 года № 214-р» распадётся на две
# несовпадения.
REF_PATTERNS = [
    ("order", re.compile(
        r"приказ\w*\s+(?:ректора\s+)?(?:от\s+\d{1,2}\s+\S+\s+\d{4}\s*(?:года)?\s*)?"
        r"№\s*([\d]+(?:-[А-Яа-яA-Za-z]{1,4})?)", re.I)),
    ("order", re.compile(
        r"приказ\w*\s+№\s*([\d]+(?:-[А-Яа-яA-Za-z]{1,4})?)", re.I)),
    ("protocol", re.compile(
        r"протокол\w*\s+№\s*(\d+)", re.I)),
    # Общая схема вида «№ N» сознательно НЕ используется. Она давала
    # из «приказ № 214-р» ещё и «протокол № 214»: приказ превращался в
    # протокол, и в графе появлялись узлы-призраки, по которым потом
    # шёл обход и приводил к «найденным» документам, которых там нет.
    # Конкретные шаблоны выше покрывают и приказы, и протоколы.
]

# Вопрос упоминает документ по номеру: «приказ 214-р», «протокол 14»
Q_ORDER = re.compile(r"(?:приказ\w*|№)\s*(\d+(?:-[А-Яа-яA-Za-z]{1,4})?)", re.I)
Q_PROTOCOL = re.compile(r"протокол\w*\s*№?\s*(\d+)", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_node (
  node_id   INTEGER PRIMARY KEY,
  kind      TEXT NOT NULL,
  label     TEXT NOT NULL,
  norm      TEXT NOT NULL,
  doc_id    INTEGER,
  chunk_id  INTEGER,
  payload   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS graph_node_norm ON graph_node(kind, norm);
CREATE INDEX IF NOT EXISTS graph_node_doc ON graph_node(doc_id);

CREATE TABLE IF NOT EXISTS graph_edge (
  src INTEGER NOT NULL REFERENCES graph_node(node_id) ON DELETE CASCADE,
  dst INTEGER NOT NULL REFERENCES graph_node(node_id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS graph_edge_dst ON graph_edge(dst);
CREATE INDEX IF NOT EXISTS graph_edge_src ON graph_edge(src);
"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _upsert_node(conn, kind, label, doc_id=None, chunk_id=None, payload=None):
    norm = _norm(label)
    row = conn.execute(
        "SELECT node_id FROM graph_node WHERE kind = ? AND norm = ?",
        (kind, norm)).fetchone()
    if row:
        return int(row["node_id"])
    cur = conn.execute(
        "INSERT INTO graph_node(kind, label, norm, doc_id, chunk_id, payload)"
        " VALUES(?,?,?,?,?,?)",
        (kind, label, norm, doc_id, chunk_id, payload))
    return int(cur.lastrowid)


def _edge(conn, src, dst, kind):
    if src and dst and src != dst:
        conn.execute("INSERT OR IGNORE INTO graph_edge(src, dst, kind)"
                     " VALUES(?,?,?)", (src, dst, kind))


def find_refs(text: str):
    """Возвращает список (kind, label) из текста."""
    out = []
    for kind, pat in REF_PATTERNS:
        for m in pat.finditer(text or ""):
            num = m.group(1)
            if kind == "order":
                out.append(("order", "приказ № " + num))
            else:
                out.append(("protocol", "протокол № " + num))
    seen, uniq = set(), []
    for k, lab in out:
        key = (k, lab.lower())
        if key not in seen:
            seen.add(key)
            uniq.append((k, lab))
    return uniq


def build(conn, root=None):
    """Строит граф по всему корпусу. Идемпотентно — можно перезапускать."""
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM graph_edge")
    conn.execute("DELETE FROM graph_node")

    docs = 0
    sections = 0
    refs = 0

    for d in conn.execute("SELECT doc_id, rel_path, title FROM doc"):
        doc_id = int(d["doc_id"])
        docs += 1
        doc_node = _upsert_node(conn, "doc", d["title"] or d["rel_path"],
                                doc_id=doc_id)

        rows = conn.execute(
            "SELECT chunk_id, ord, heading, text FROM doc_chunk"
            " WHERE doc_id = ? ORDER BY ord", (doc_id,)).fetchall()
        current_head = ""
        current_node = doc_node
        head_seen = set()

        for r in rows:
            heading = (r["heading"] or "").strip()
            if heading and heading != current_head:
                if heading not in head_seen:
                    sec = _upsert_node(conn, "section", heading, doc_id=doc_id,
                                       chunk_id=int(r["chunk_id"]))
                    _edge(conn, doc_node, sec, "contains")
                    head_seen.add(heading)
                    current_node = sec
                    sections += 1
                current_head = heading

            for kind, label in find_refs(r["text"]):
                ref_node = _upsert_node(conn, kind, label, payload=doc_id and str(doc_id))
                _edge(conn, current_node, ref_node, "mentions")
                refs += 1

        # Документ в целом может ссылаться на приказ, даже если номер
        # стоит в шапке, а не в тексте раздела.
        header_text = " ".join((r["heading"] or "") for r in rows[:5])
        for kind, label in find_refs(header_text):
            ref_node = _upsert_node(conn, kind, label)
            _edge(conn, doc_node, ref_node, "mentions")

    conn.commit()
    stats = {
        "docs": docs,
        "nodes": conn.execute("SELECT COUNT(*) FROM graph_node").fetchone()[0],
        "edges": conn.execute("SELECT COUNT(*) FROM graph_edge").fetchone()[0],
        "sections": sections,
        "refs": refs,
    }
    db.set_meta(conn, "graph_built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    return stats


def _seed_nodes(conn, question):
    """Находит в вопросе узлы, с которых начинается обход графа."""
    seeds = []
    q = question or ""
    for kind, pat in (("order", Q_ORDER), ("protocol", Q_PROTOCOL)):
        for m in pat.finditer(q):
            label = ("приказ № " if kind == "order" else "протокол № ") + m.group(1)
            row = conn.execute(
                "SELECT node_id FROM graph_node WHERE kind = ? AND norm = ?",
                (kind, _norm(label))).fetchone()
            if row:
                seeds.append(("номер в вопросе: " + label, int(row["node_id"])))

    # Название документа: «что написано в положении о приёме».
    # Узел документа лежит в graph_node, а название — в doc, поэтому
    # берём через соединение, а не из одной таблицы.
    #
    # Сравнение идёт по ОСНОВАМ слов, а не по сырым словам. Иначе
    # «положении» из вопроса и «положение» из названия не совпадут:
    # подстроками они друг друга не содержат, и документ не найдётся
    # ни при каком числе совпадений.
    from .retrieve import stem_ru

    q_stems = set()
    for w in re.findall(r"[А-Яа-яЁёA-Za-z]{5,}", q):
        s = stem_ru(w.lower())
        if len(s) >= 5:
            q_stems.add(s)
    if not q_stems:
        return seeds

    scored = []
    for row in conn.execute(
            "SELECT n.node_id, d.title FROM graph_node n"
            " JOIN doc d ON d.doc_id = n.doc_id WHERE n.kind = 'doc'"):
        title_stems = set()
        for w in re.findall(r"[а-яёa-z]{5,}", (row["title"] or "").lower()):
            s = stem_ru(w)
            if len(s) >= 5:
                title_stems.add(s)
        if not title_stems:
            continue
        hits = q_stems & title_stems
        if hits:
            scored.append((len(hits), int(row["node_id"]),
                           (row["title"] or "")[:50], sorted(hits)[:3]))

    scored.sort(reverse=True)
    # Два документа достаточно: вопрос почти всегда про один, а второй
    # берётся на случай, если название совпало случайно.
    for count, node_id, title, hits in scored[:2]:
        seeds.append(("название: " + title + " (совпало: " + ", ".join(hits) + ")",
                      node_id))
    return seeds


def expand(conn, question: str, hops: int = 1, limit_docs: int = 6):
    """Обходит граф от узлов, упомянутых в вопросе.

    Возвращает (список найденных фрагментов, описание обхода).
    Собранное помечается: это найдено по связям, а не по совпадению
    слов, и ассистент должен сказать об этом пользователю.
    """
    seeds = _seed_nodes(conn, question)
    if not seeds:
        return [], {"seeds": 0, "docs": [], "reason": "в вопросе нет опознаваемых ссылок"}

    visited = {nid for _lab, nid in seeds}
    frontier = list(visited)
    doc_ids = []

    for _hop in range(max(0, hops)):
        nxt = []
        for nid in frontier:
            rows = conn.execute(
                "SELECT dst, kind FROM graph_edge WHERE src = ?"
                " UNION SELECT src, kind FROM graph_edge WHERE dst = ?",
                (nid, nid)).fetchall()
            for r in rows:
                other = int(r["dst"] if int(r["dst"]) != nid else r["src"])
                if other in visited:
                    continue
                visited.add(other)
                nxt.append(other)
                node = conn.execute(
                    "SELECT kind, doc_id, label FROM graph_node WHERE node_id = ?",
                    (other,)).fetchone()
                if node and node["doc_id"] and node["kind"] in ("doc", "section"):
                    if int(node["doc_id"]) not in doc_ids:
                        doc_ids.append(int(node["doc_id"]))
        frontier = nxt
        if len(doc_ids) >= limit_docs:
            break

    doc_ids = doc_ids[:limit_docs]
    if not doc_ids:
        return [], {"seeds": len(seeds), "docs": [],
                    "reason": "от ссылок не дошли до документов"}

    marks = ",".join("?" * len(doc_ids))
    rows = conn.execute(
        f"SELECT c.chunk_id, c.doc_id, c.ord, c.heading, c.text,"
        f" d.title, d.rel_path, d.doc_type, d.is_demo"
        f" FROM doc_chunk c JOIN doc d ON d.doc_id = c.doc_id"
        f" WHERE c.doc_id IN ({marks}) ORDER BY c.doc_id, c.ord"
        f" LIMIT 60", doc_ids).fetchall()

    chunks = [{
        "chunk_id": int(r["chunk_id"]), "doc_id": int(r["doc_id"]),
        "doc_title": r["title"], "rel_path": r["rel_path"],
        "doc_type": r["doc_type"], "is_demo": bool(r["is_demo"]),
        "heading": r["heading"], "text": r["text"],
        "score": 0.0, "coverage": 0.0,
        "fts_rank": None, "vec_rank": None,
        "found_by": "graph", "via_graph": True,
    } for r in rows]

    info = {
        "seeds": len(seeds),
        "seed_labels": [lab for lab, _ in seeds][:4],
        "docs": doc_ids,
        "doc_titles": conn.execute(
            f"SELECT title FROM doc WHERE doc_id IN ({marks})",
            doc_ids).fetchall()[0]["title"] if doc_ids else None,
        "chunks": len(chunks),
    }
    return chunks, info


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Граф связей документов")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    stats = build(conn)

    if args.json:
        print(__import__("json").dumps(stats, ensure_ascii=False))
    else:
        print(f"Документов: {stats['docs']}")
        print(f"Узлов:      {stats['nodes']} (разделов {stats['sections']})")
        print(f"Связей:     {stats['edges']} (упоминаний {stats['refs']})")
        print()
        print("Узлы-ссылки:")
        for r in conn.execute(
                "SELECT n.kind, n.label, COUNT(e.src) deg FROM graph_node n"
                " LEFT JOIN graph_edge e ON e.dst = n.node_id"
                " WHERE n.kind IN ('order','protocol')"
                " GROUP BY n.node_id ORDER BY deg DESC LIMIT 12"):
            print(f"  {r['kind']:9} {r['label']:28} упоминаний: {r['deg']}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())