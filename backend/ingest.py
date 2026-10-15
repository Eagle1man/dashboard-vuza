# -*- coding: utf-8 -*-
"""
Загрузка корпуса: обход папки knowledge/ -> текст -> куски -> векторы.

Идемпотентность. Документ переиндексируется, только если изменился
его sha256 или он не найден в базе. Это важно не для экономии:
пересчёт эмбеддингов на ноутбуке ректора занимает минуты, и если
каждый запуск заново пересчитывал всю папку, то перезапуск
дашборда превращался бы в ожидание.

Переиндексация одного файла при этом пересчитывает только его
куски, а не весь корпус.
"""
import hashlib
import time
from pathlib import Path

from . import chunking
from . import config
from . import db
from . import embed
from . import extract

BATCH = 16


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def iter_documents(root: Path = None):
    root = Path(root or config.KNOWLEDGE_DIR)
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in config.SUPPORTED_EXT:
            continue
        if path.name.startswith("~$"):     # временные файлы Excel/Word
            continue
        yield path


def _delete_doc(conn, doc_id: int):
    conn.execute("DELETE FROM doc_block WHERE doc_id = ?", (doc_id,))
    for row in conn.execute(
            "SELECT chunk_id FROM doc_chunk WHERE doc_id = ?", (doc_id,)):
        conn.execute("DELETE FROM chunk_fts WHERE chunk_id = ?", (row[0],))
        conn.execute("DELETE FROM chunk_vec WHERE chunk_id = ?", (row[0],))
    conn.execute("DELETE FROM doc_chunk WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM doc_page WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM doc WHERE doc_id = ?", (doc_id,))


def index_file(conn, path: Path, is_demo: bool, force=False) -> dict:
    rel = path.relative_to(config.KNOWLEDGE_DIR).as_posix()
    sha = _sha256(path)

    existing = conn.execute("SELECT doc_id, sha256 FROM doc WHERE rel_path = ?",
                            (rel,)).fetchone()
    if existing and existing["sha256"] == sha and not force:
        return {"path": rel, "status": "unchanged", "chunks": 0}

    text, page_info = extract.extract_text_with_pages(path)
    if not text or not text.strip():
        if existing:
            _delete_doc(conn, int(existing["doc_id"]))
            conn.commit()
        return {"path": rel, "status": "empty", "chunks": 0}

    chunks = chunking.chunk_document(text)
    if not chunks:
        return {"path": rel, "status": "no-chunks", "chunks": 0}

    # Внутри одного документа одинаковые куски не хранятся дважды:
    # нарезка по перекрытию иногда даёт повтор, а поисковой ценности
    # у второго вхождения нет.
    unique_chunks, seen_hashes = [], set()
    for ch in chunks:
        if ch["chunk_hash"] in seen_hashes:
            continue
        seen_hashes.add(ch["chunk_hash"])
        unique_chunks.append(ch)
    dropped = len(chunks) - len(unique_chunks)

    if existing:
        _delete_doc(conn, int(existing["doc_id"]))

    title = extract.title_from(path, text)
    blocks = db.detect_blocks(rel, title, text)
    cur = conn.execute(
        "INSERT INTO doc(rel_path, title, doc_type, sha256, n_pages, n_chunks,"
        " is_demo, added_at, block_id) VALUES(?,?,?,?,?,?,?,?,?)",
        (rel, title, extract.doc_type(path), sha, text.count("[стр. ") or None,
         len(unique_chunks), 1 if is_demo else 0,
         time.strftime("%Y-%m-%d %H:%M:%S"), blocks[0] if blocks else None))
    doc_id = int(cur.lastrowid)

    for block_id in blocks:
        conn.execute(
            "INSERT OR IGNORE INTO doc_block(doc_id, block_id) VALUES(?,?)",
            (doc_id, block_id))

    for pg in (page_info or []):
        conn.execute(
            "INSERT OR REPLACE INTO doc_page"
            "(doc_id, page_no, kind, n_chars, n_images, n_tables)"
            " VALUES(?,?,?,?,?,?)",
            (doc_id, int(pg.get("page_no", 0)), pg.get("kind", "text"),
             int(pg.get("n_chars", 0)), int(pg.get("n_images", 0)),
             int(pg.get("n_tables", 0))))

    # inserted держит пары (chunk_id, текст) в том же порядке, в котором
    # считаются эмбеддинги. Раньше id брались повторным SELECT'ом с
    # нарезкой по позиции — как только один кусок отбрасывался,
    # вектор прикреплялся бы к соседнему куску.
    inserted = []
    for i, ch in enumerate(unique_chunks):
        ccur = conn.execute(
            "INSERT INTO doc_chunk(doc_id, ord, heading, text, n_chars, chunk_hash)"
            " VALUES(?,?,?,?,?,?)",
            (doc_id, i, ch["heading"], ch["text"], ch["n_chars"],
             ch["chunk_hash"]))
        chunk_id = int(ccur.lastrowid)
        conn.execute(
            "INSERT INTO chunk_fts(text, heading, chunk_id) VALUES(?,?,?)",
            (ch["text"], ch["heading"] or "", chunk_id))
        inserted.append((chunk_id, ch["text"]))

    conn.commit()

    # Векторы считаем пачками: по одному на кусок при корпусе в несколько
    # тысяч документов упирается в HTTP-задержку.
    backend = "none"
    for start in range(0, len(inserted), BATCH):
        batch = inserted[start:start + BATCH]
        vecs, backend = embed.embed_texts([text for _cid, text in batch])
        for (chunk_id, _text), vec in zip(batch, vecs):
            conn.execute(
                "INSERT OR REPLACE INTO chunk_vec(chunk_id, doc_id, dim, vec, backend)"
                " VALUES(?,?,?,?,?)",
                (chunk_id, doc_id, int(vec.shape[0]), embed.to_blob(vec), backend))
        conn.commit()

    return {"path": rel, "status": "indexed", "chunks": len(inserted),
            "dropped_dupes": dropped, "backend": backend, "title": title,
            "block_id": blocks[0] if blocks else None, "blocks": blocks,
            "pages": len(page_info or []),
            "graphic_pages": sum(1 for pg in (page_info or [])
                                 if pg.get("kind") == "image")}


def ingest(force=False, prune=False, root: Path = None) -> dict:
    root = Path(root or config.KNOWLEDGE_DIR)
    config.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    db.init_db(conn)

    report = {"root": str(root), "indexed": [], "unchanged": [],
              "empty": [], "failed": [], "pruned": []}

    for path in iter_documents(root):
        try:
            is_demo = "demo" in path.parts
            res = index_file(conn, path, is_demo=is_demo, force=force)
            # один раз в свой список: раньше res попадал и в общий
            # список по статусу, и в report['indexed'], из-за чего
            # в отчёте каждый файл печатался дважды
            report.setdefault(res["status"], []).append(res)
        except Exception as exc:
            report["failed"].append({"path": path.name, "error": str(exc)})

    if prune:
        present = {p.relative_to(root).as_posix() for p in iter_documents(root)}
        for row in conn.execute("SELECT doc_id, rel_path FROM doc").fetchall():
            if row["rel_path"] not in present:
                _delete_doc(conn, int(row["doc_id"]))
                report["pruned"].append(row["rel_path"])
        conn.commit()

    db.set_meta(conn, "last_ingest", time.strftime("%Y-%m-%d %H:%M:%S"))

    # Граф связей строится после индексации: он опирается на заголовки
    # разделов и номера приказов, которые появляются только вместе с
    # кусками. Ошибка здесь не должна срывать индексацию — корпус
    # уже загружен, а без графа поиск просто работает чуть хуже.
    try:
        from . import graph
        report["graph"] = graph.build(conn)
    except Exception as exc:
        report["graph"] = {"error": str(exc)}

    # Схлопываем журнал WAL в основной файл. Иначе после долгой работы
    # основной файл оказывается меньше журнала, и копия одного файла
    # (которую делает backup.bat) требует ещё и пары -wal/-shm.
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    except Exception:
        pass

    report["totals"] = {
        "docs": conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0],
        "chunks": conn.execute("SELECT COUNT(*) FROM doc_chunk").fetchone()[0],
        "vectors": conn.execute("SELECT COUNT(*) FROM chunk_vec").fetchone()[0],
        "bytes": (config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0),
    }
    backends = [r[0] for r in conn.execute(
        "SELECT DISTINCT backend FROM chunk_vec").fetchall()]
    report["vector_backend"] = backends[0] if len(backends) == 1 else backends
    conn.close()
    return report


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Индексация документов")
    ap.add_argument("--force", action="store_true", help="пересчитать всё")
    ap.add_argument("--prune", action="store_true",
                    help="убрать из индекса удалённые файлы")
    args = ap.parse_args()

    rep = ingest(force=args.force, prune=args.prune)
    print(f"Папка: {rep['root']}")
    print(f"Новых/обновлённых: {len(rep['indexed'])}, "
          f"без изменений: {len(rep['unchanged'])}, "
          f"удалено: {len(rep['pruned'])}, "
          f"не удалось: {len(rep['failed'])}")
    for item in rep["indexed"]:
        print(f"  + {item['path']}: кусков {item['chunks']} "
              f"({item.get('backend', '?')})")
    for item in rep["failed"]:
        print(f"  ! {item['path']}: {item['error']}")
    t = rep["totals"]
    print(f"Итого документов {t['docs']}, кусков {t['chunks']}, "
          f"векторов {t['vectors']}, база {t['bytes'] // 1024} КБ")
    print(f"Режим векторов: {rep['vector_backend']}")
    return 1 if rep["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())