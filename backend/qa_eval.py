# -*- coding: utf-8 -*-
"""Замер качества: находит ли поиск факт и отвечает ли модель по нему.

Два режима, и это важно:

* `--retrieval-only` не зовёт модель вообще. Он отвечает на вопрос
  «доходит ли нужное число до модели». Если факт не попал в контекст,
  никакая LoRA не поможет — модель его не увидит.
* `--model <тег>` прогоняет боевой путь `llm.answer` (тот же промпт,
  тот же grounding) и считает, попала ли цифра в ответ, есть ли ссылка
  и не выдуманы ли числа.

Запуск:

    python -m backend.qa_eval --split test --retrieval-only
    python -m backend.qa_eval --split dev --model qwen2.5:7b-instruct
"""
import argparse
import json
import pathlib
import re
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config, db, llm, retrieve  # noqa: E402

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
REFUSAL_MARKS = ("не найден", "нет данных", "не содержится", "отсутству")


def norm_num(text):
    return str(text or "").replace("\u00a0", "").replace(" ", "").replace(",", ".")


def norm_text(text):
    return re.sub(r"\s+", " ", str(text or "")).lower()


def numbers_in(text):
    return {norm_num(m) for m in NUM_RE.findall(text or "")}


def load_questions(path, split=None, task=None, limit=None):
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    if split:
        rows = [r for r in rows if r["split"] == split]
    if task:
        rows = [r for r in rows if r["task"] == task]
    if limit:
        rows = rows[:limit]
    return rows


def evaluate(args):
    conn = db.connect()
    db.init_db(conn)
    questions = load_questions(pathlib.Path(args.questions),
                               args.split, args.task, args.limit)
    if not questions:
        print("вопросов нет — проверьте --split/--task")
        return 1

    results, failures = [], []
    for i, q in enumerate(questions, 1):
        if args.oracle:
            chunks, stats = q["context"], {"fusion": "oracle"}
        else:
            chunks, stats = retrieve.search(conn, q["question"],
                                            top_k=args.top_k)
        texts = " ".join(c["text"] for c in chunks)
        paths = [] if args.oracle else [c["rel_path"] for c in chunks]

        doc_hit = None if args.oracle else any(
            p in paths for p in q["expected_paths"])
        operands = list(q.get("operands") or [])
        value_hit = None
        if operands or q.get("expected_value"):
            needles = operands or [q["expected_value"]]
            value_hit = all(norm_num(n) in norm_num(texts) for n in needles)

        item = {
            "id": q["id"], "task": q["task"], "question": q["question"],
            "doc_hit": doc_hit, "value_hit": value_hit,
            "retrieved": [{"path": c["rel_path"], "heading": c.get("heading"),
                           "score": c.get("score")} for c in chunks],
            "fusion": stats.get("fusion"),
        }

        if args.model:
            started = time.time()
            answer, meta = llm.answer(q["question"], chunks, model=args.model)
            item["latency_ms"] = int((time.time() - started) * 1000)
            item["answer"] = answer
            item["llm"] = bool(meta.get("llm"))
            item["grounding"] = meta.get("grounding")
            low = answer.lower()
            item["citation"] = bool(re.search(r"\[\d", answer))
            if q["task"] == "refusal":
                item["refusal_ok"] = any(m in low for m in REFUSAL_MARKS)
            if q.get("expected_value") or operands:
                item["answer_value_hit"] = norm_num(q.get("expected_value")) in \
                    norm_num(answer) if q.get("expected_value") else None
            ctx_nums = numbers_in(texts)
            expected_nums = {norm_num(n) for n in (operands or [])}
            if q.get("expected_value"):
                expected_nums.add(norm_num(q["expected_value"]))
            item["invented"] = sorted(numbers_in(answer)
                                      - ctx_nums - expected_nums)

        results.append(item)
        if doc_hit is False or value_hit is False:
            failures.append(item)
        if i % 25 == 0:
            print(f"  {i}/{len(questions)}", flush=True)

    # ---------- сводка ----------
    n = len(results)
    def rate(key, subset=None):
        pool = results if subset is None else subset
        part = [r for r in pool if r.get(key) is not None]
        return round(sum(bool(r[key]) for r in part) / len(part), 3) if part else None

    value_rows = [r for r in results if r["value_hit"] is not None]
    summary = {
        "questions": n,
        "split": args.split or "all",
        "model": args.model or None,
        "oracle_context": bool(args.oracle),
        "doc_hit_rate": rate("doc_hit"),
        "value_hit_rate": rate("value_hit", value_rows),
        "value_questions": len(value_rows),
        "failures": len(failures),
        "by_task": {},
    }
    for task in sorted({r["task"] for r in results}):
        part = [r for r in results if r["task"] == task]
        vpart = [r for r in part if r["value_hit"] is not None]
        summary["by_task"][task] = {
            "count": len(part),
            "doc_hit": rate("doc_hit", part),
            "value_hit": rate("value_hit", vpart),
        }
    if args.model:
        summary.update({
            "citation_rate": rate("citation"),
            "refusal_rate": rate("refusal_ok"),
            "answer_value_rate": rate("answer_value_hit"),
            "invented_total": sum(len(r.get("invented") or []) for r in results),
            "latency_median_ms": int(statistics.median(
                r.get("latency_ms", 0) for r in results)),
        })

    mode = ("oracle-" if args.oracle else "") + ("llm" if args.model else "retrieval")
    out = pathlib.Path(args.out) if args.out else (
        ROOT / "docs" / "eval" / f"report-{args.split or 'all'}-{mode}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary,
                               "failures": failures[:60],
                               "results": results}, ensure_ascii=False,
                              indent=1), encoding="utf-8")
    conn.close()

    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("отчёт:", out)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(ROOT / "docs" / "eval" / "questions.jsonl"))
    ap.add_argument("--split", choices=["train", "dev", "test"])
    ap.add_argument("--task", choices=["extraction", "comparison", "refusal", "structure"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--top-k", type=int, default=config.CONTEXT_CHUNKS)
    ap.add_argument("--model", help="тег Ollama; без него — только поиск")
    ap.add_argument("--oracle", action="store_true",
                    help="брать контекст из вопроса, а не из поиска: "
                         "отделяет качество модели от качества поиска")
    ap.add_argument("--out")
    args = ap.parse_args()
    if not args.model:
        args.retrieval_only = True
    return evaluate(args)


if __name__ == "__main__":
    raise SystemExit(main())
