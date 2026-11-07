# -*- coding: utf-8 -*-
"""Собирает ноутбук QLoRA заново, а не правит JSON руками.

Почему пересборка. Прошлый ноутбук ставил unsloth с пином под torch 2.4
и cu121, обучал две эпохи на 2048 токенах и первым делом пытался сделать
GGUF. На Kaggle это три отказа: образ обновился, данных хватает на одну
эпоху, а merge 14B в fp16 требует 28 ГБ при 20 ГБ в /kaggle/working.

    python docs/kaggle/make_notebook.py
"""
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "finetune_qwen14b.ipynb"

SYSTEM = ("Ты — «Аналитик ректора» Южного университета (ИУБиП). Отвечай "
          "только по фрагментам, каждое утверждение о фактах сопровождай "
          "ссылкой [1]. Если ответа во фрагментах нет — так и напиши. "
          "Цифры приводи как в источнике. 3–6 предложений, по-русски.")

cells = []


def md(text):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)})


def code(text):
    cells.append({"cell_type": "code", "execution_count": None, "metadata": {},
                  "outputs": [], "source": text.splitlines(keepends=True)})


md("""# QLoRA для ассистента ректора (Qwen2.5-14B)

**Что делает ноутбук.** Дообучает Qwen2.5-14B-Instruct на парах
«вопрос → ответ по фрагменту» и сохраняет **адаптер**. GGUF — отдельным
шагом и только если хватает диска.

**Чего он не делает.** Не вшивает цифры мониторинга в веса: они меняются
каждый год, а проверяемость важнее памяти. LoRA учит формат ответа,
ссылку на источник и отказ, когда данных нет; сами числа остаются в RAG.

**Что изменилось после замера.** Вопросы и ответы теперь в боевом
формате (`prompts.SYSTEM`, ссылки `[1]`), данные разделены по документам
(829 train / 182 dev / 207 test), обучение — одна эпоха на 1024 токенах:
в датасете максимум ~490 токенов, вторая эпоха была бы переобучением.

Перед запуском добавьте датасет `orel1986/monitoring-qa` и включите GPU.
""")

md("## 1. Окружение\n")
code("""# Unsloth без жёсткого пина: Kaggle меняет образ (torch/cu), и
# зафиксированный cu121-torch240 ломается при очередном обновлении.
!pip install -q unsloth
!pip install -q --upgrade --no-deps trl peft accelerate bitsandbytes

import os
# Одна GPU. T4×2 расходует квоту вдвое, а 14B в 4 битах помещается
# в 16 ГБ. Переменную ставим ДО первого обращения к CUDA.
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

import subprocess
import torch
print("GPU:", torch.cuda.get_device_name(0))
print("VRAM, ГБ:", round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1))
print(subprocess.run(["pip", "freeze"], capture_output=True, text=True).stdout[-1200:])
""")

md("## 2. Модель и токенизатор\n")
code("""from unsloth import FastLanguageModel

# 1024 достаточно: самый длинный пример ~490 токенов. Меньше контекста —
# быстрее шаг и меньше памяти, а качество на этих данных не страдает.
MAX_SEQ = 1024

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen2.5-14B-Instruct-bnb-4bit",
    max_seq_length=MAX_SEQ,
    load_in_4bit=True,
    dtype=None,
)
""")

md("## 3. LoRA-адаптер\n")
code("""model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    lora_alpha=32,
    lora_dropout=0.05,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=42,
)
""")

md("""## 4. Данные

Формат — готовые `messages` (system / user / assistant), ровно как в бою.
Проверяем, что каждый пример содержит фрагмент и ссылку: если датасет
собран другим промптом, адаптер выучит не ту роль.
""")
code("""import json
import pathlib
from datasets import Dataset

path = pathlib.Path("/kaggle/input/monitoring-qa/train.jsonl")
if not path.exists():
    matches = list(pathlib.Path("/kaggle/input").rglob("train.jsonl"))
    assert matches, "train.jsonl не найден в /kaggle/input"
    path = matches[0]

rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()]
bad = [r for r in rows
       if len(r["messages"]) != 3
       or "ВОПРОС:" not in r["messages"][1]["content"]
       or "[" not in r["messages"][2]["content"]]
assert not bad, f"примеров не в боевом формате: {len(bad)}"
print("примеров:", len(rows))
print(rows[0]["messages"][1]["content"][:280])


def to_text(row):
    return {"text": tokenizer.apply_chat_template(
        row["messages"], tokenize=False, add_generation_prompt=False)}


dataset = Dataset.from_list(rows).map(to_text, remove_columns=["messages"])
print("токенов в первом примере:", len(tokenizer(dataset[0]["text"])["input_ids"]))
""")

md("## 5. Обучение\n")
code("""from trl import SFTTrainer
from transformers import TrainingArguments

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=2,
        gradient_accumulation_steps=8,   # эффективный batch 16, ~52 шага
        num_train_epochs=1,              # 829 примеров; вторая эпоха = переобучение
        learning_rate=2e-4,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        optim="adamw_8bit",
        weight_decay=0.01,
        logging_steps=5,
        save_steps=50,
        save_total_limit=2,
        seed=42,
        output_dir="/kaggle/working/checkpoints",
        report_to="none",
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
    ),
)
stats = trainer.train()
print(stats)
""")

md("""## 6. Адаптер — сразу и надёжно

Адаптер 14B весит ~300 МБ и сохраняется всегда. Это главный артефакт:
даже если дальше что-то не хватит по диску, обучение не потеряно.
""")
code("""model.save_pretrained("/kaggle/working/lora_adapter")
tokenizer.save_pretrained("/kaggle/working/lora_adapter")
!cd /kaggle/working && zip -qr lora_adapter.zip lora_adapter && ls -lh lora_adapter.zip
""")

md("""## 7. Замер: база и адаптер

Одни и те же вопросы test, один и тот же контекст. Сначала отвечает
адаптер, потом адаптер отключается и отвечает база. Числа считает код:
`numeric_exact`, `citation_present`, `refusal_correct`,
`hallucinated_number_rate`. Это и есть приёмка: без сравнения обучение
не принимается.
""")
code("""import collections
import json
import pathlib
import random
import re

qpath = pathlib.Path("/kaggle/input/monitoring-qa/questions.jsonl")
if not qpath.exists():
    matches = list(pathlib.Path("/kaggle/input").rglob("questions.jsonl"))
    assert matches, "questions.jsonl не найден в /kaggle/input"
    qpath = matches[0]
questions = [json.loads(line) for line in
             qpath.read_text(encoding="utf-8").splitlines() if line.strip()]
test = [q for q in questions if q.get("split") == "test"]
random.Random(42).shuffle(test)
by_task = collections.defaultdict(list)
for q in test:
    by_task[q["task"]].append(q)
sample = []
for task, limit in (("extraction", 24), ("comparison", 10),
                    ("refusal", 8), ("structure", 6)):
    sample += by_task.get(task, [])[:limit]
print("вопросов в замере:", len(sample),
      collections.Counter(q["task"] for q in sample))


def generate(messages, max_new_tokens=220):
    inputs = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        return_tensors="pt").to("cuda")
    out = model.generate(input_ids=inputs, max_new_tokens=max_new_tokens,
                         do_sample=False)
    return tokenizer.decode(out[0][inputs.shape[1]:],
                            skip_special_tokens=True).strip()


def numbers(text):
    text = re.sub(r"\\[\\d+(?:\\s*,\\s*\\d+)*\\]", " ", str(text))
    out = set()
    for match in re.findall(r"-?\\d+(?:[.,]\\d+)?", text.replace(" ", "")):
        try:
            out.add(round(float(match.replace(",", ".")), 2))
        except ValueError:
            pass
    return out


def expected_number(value):
    if value is None:
        return None
    try:
        return round(float(str(value).replace(" ", "").replace(",", ".")), 2)
    except ValueError:
        return None


REFUSAL = ("не найден", "нет данных", "не содержится", "не удалось",
           "отсутств", "не указан")


def is_refusal(text):
    low = str(text).lower()
    return any(marker in low for marker in REFUSAL)


def score(question, answer):
    expected = expected_number(question.get("expected_value"))
    row = {"id": question["id"], "task": question["task"],
           "answer": answer, "expected": expected}
    row["numeric_exact"] = int(expected is not None and expected in numbers(answer))
    row["citation_present"] = int(bool(re.search(r"\\[\\d+", answer)))
    row["refusal_correct"] = int(question["task"] == "refusal"
                                 and is_refusal(answer))
    row["false_refusal"] = int(question["task"] != "refusal"
                               and is_refusal(answer))
    context = " ".join(chunk.get("text", "")
                       for chunk in (question.get("context") or []))
    row["hallucinated_number"] = int(bool(
        numbers(answer) - numbers(context) - {2024, 2025}))
    return row


def summarize(rows):
    result = {"n": len(rows)}
    numeric = [row for row in rows if row["expected"] is not None]
    refusals = [row for row in rows if row["task"] == "refusal"]
    normal = [row for row in rows if row["task"] != "refusal"]
    result["numeric_exact_rate"] = (
        round(sum(row["numeric_exact"] for row in numeric) / len(numeric), 3)
        if numeric else None)
    result["citation_present_rate"] = round(
        sum(row["citation_present"] for row in rows) / len(rows), 3)
    result["refusal_correct_rate"] = (
        round(sum(row["refusal_correct"] for row in refusals) / len(refusals), 3)
        if refusals else None)
    result["false_refusal_rate"] = (
        round(sum(row["false_refusal"] for row in normal) / len(normal), 3)
        if normal else None)
    result["hallucinated_number_rate"] = round(
        sum(row["hallucinated_number"] for row in rows) / len(rows), 3)
    return result


FastLanguageModel.for_inference(model)
adapter_rows = [score(q, generate(q["messages"][:2])) for q in sample]

disabled = hasattr(model, "disable_adapter_layers")
base_rows = []
if disabled:
    model.disable_adapter_layers()
    FastLanguageModel.for_inference(model)
    base_rows = [score(q, generate(q["messages"][:2])) for q in sample]
else:
    print("disable_adapter_layers недоступен: база не замерена")

report = {"sample": len(sample),
          "adapter": summarize(adapter_rows),
          "base": summarize(base_rows) if base_rows else None,
          "details": {"adapter": adapter_rows, "base": base_rows}}
pathlib.Path("/kaggle/working/eval_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"adapter": report["adapter"],
                  "base": report["base"]}, ensure_ascii=False, indent=1))
""")

md("""## 8. GGUF — только при достатке диска

`save_pretrained_gguf` сначала сливает 14B в fp16 (~28 ГБ). В
`/kaggle/working` только 20 ГБ, поэтому merge идёт в `/tmp`, а в
`/kaggle/working` копируется только готовый `.gguf` (~9 ГБ). Если места
не хватает, шаг пропускается, адаптер остаётся.
""")
code("""import pathlib
import shutil

free_tmp = shutil.disk_usage("/tmp").free / 1e9
free_work = shutil.disk_usage("/kaggle/working").free / 1e9
print("свободно: /tmp", round(free_tmp, 1),
      "ГБ; /kaggle/working", round(free_work, 1), "ГБ")
try:
    if free_tmp < 35:
        print("GGUF пропущен: для merge 14B в fp16 нужно ~28 ГБ в /tmp.")
    else:
        model.save_pretrained_gguf("/tmp/gguf", tokenizer,
                                   quantization_method="q4_k_m")
        ggufs = sorted(pathlib.Path("/tmp/gguf").glob("*.gguf"))
        assert ggufs, "GGUF не создан"
        for source in ggufs:
            size_gb = source.stat().st_size / 1e9
            if size_gb + 1 < free_work:
                shutil.copy(source, "/kaggle/working/" + source.name)
                print("скопирован:", source.name, round(size_gb, 1), "ГБ")
            else:
                print("не скопирован (мало места):", source.name,
                      round(size_gb, 1), "ГБ")
except Exception as exc:
    print("GGUF не получился:", exc)
!ls -lh /kaggle/working
""")

md("""## 9. Что делать с файлами

1. Скачать `lora_adapter.zip` (~300 МБ) и `eval_report.json` — обязательно.
2. Если получился `.gguf` — скачать и его, это готовая модель для Ollama.
3. Если GGUF не получился, слить на машине с диском ~40 ГБ:

```
python - <<'PY'
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-14B-Instruct")
model = PeftModel.from_pretrained(base, "lora_adapter").merge_and_unload()
model.save_pretrained("merged")
PY
```

4. Дальше — `llama.cpp` → `q4_k_m` → `Modelfile` → `ollama create rector-qwen`.
5. Замер уже лежит в `eval_report.json`. Повторить локально можно так:

```
python -m backend.qa_eval --split test --oracle --limit 20 --model qwen2.5:14b
python -m backend.qa_eval --split test --oracle --limit 20 --model rector-qwen
```

Адаптер принимается, только если `citation_present` и `refusal_correct`
выросли, `numeric_exact` не упал, а `hallucinated_number_rate` не вырос.
""")

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python",
                       "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}
OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("записан:", OUT, "| ячеек:", len(cells))
