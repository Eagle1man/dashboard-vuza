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
(608 train / 180 dev / 185 test), обучение — одна эпоха на 1024 токенах:
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
       or "Фрагмент:" not in r["messages"][1]["content"]
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
        gradient_accumulation_steps=8,   # эффективный batch 16, ~38 шагов
        num_train_epochs=1,              # 608 примеров; вторая эпоха = переобучение
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

md("""## 7. GGUF — только при достатке диска

`save_pretrained_gguf` сначала сливает модель в fp16: для 14B это ~28 ГБ,
а `/kaggle/working` даёт 20 ГБ. Поэтому шаг не обязательный: если места
мало, он честно пропускается, а адаптер остаётся. Слить и
сквантовать можно на машине с диском ~40 ГБ.
""")
code("""import shutil

free_gb = shutil.disk_usage("/kaggle/working").free / 1e9
print("свободно, ГБ:", round(free_gb, 1))
if free_gb < 35:
    print("GGUF пропущен: для merge 14B в fp16 нужно ~28 ГБ. "
          "Скачайте lora_adapter.zip — слить можно на сервере.")
else:
    model.save_pretrained_gguf("/kaggle/working/gguf", tokenizer,
                               quantization_method="q4_k_m")
    !ls -lh /kaggle/working/gguf
""")

md("""## 8. Проверка перед скачиванием

Смотрим, что модель отвечает по фрагменту и ставит ссылку. Это не замер
качества (он делается на dev в проекте), а проверка, что адаптер жив.
""")
code(f"""FastLanguageModel.for_inference(model)

question = "Какое место занимает ИУБиП по объёму НИОКР среди вузов области в 2025?"
context = ("[1] Документ: «Вузы Ростовской области в мониторинге, выпуск 2025», "
           "раздел: E.2 Объём НИОКР на одного НПР, тыс. руб.\\n"
           "5. ЮЖНЫЙ УНИВЕРСИТЕТ (ИУБиП) — 419.85")

messages = [
    {{"role": "system", "content": {SYSTEM!r}}},
    {{"role": "user", "content": f"Документы-фрагменты:\\n\\n{{context}}\\n\\n---\\n"
                                  f"ВОПРОС: {{question}}\\n\\n"
                                  f"ОТВЕТ (только по фрагментам выше, со ссылками [номер]):"}},
]
inputs = tokenizer.apply_chat_template(messages, tokenize=True,
                                       add_generation_prompt=True,
                                       return_tensors="pt").to("cuda")
out = model.generate(input_ids=inputs, max_new_tokens=180,
                     temperature=0.15, do_sample=True)
print(tokenizer.decode(out[0][inputs.shape[1]:], skip_special_tokens=True))
""")

md("""## 9. Что делать с файлами

1. Скачать `lora_adapter.zip` (~300 МБ) — обязательно.
2. Если получился `gguf/` — скачать и его, это готовая модель для Ollama.
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
5. **Обязательно** сравнить с базовой моделью на dev:

```
python -m backend.qa_eval --split dev --model qwen2.5:14b
python -m backend.qa_eval --split dev --model rector-qwen
```

Адаптер принимается, только если `refusal_correct` и `citation_present`
выросли, а `numeric_exact` не упал и выдуманных чисел нет.
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
