"""LoRA fine-tune bge-m3 on Omsk Oblast citizen complaints.

Стратегия: contrastive learning на silver labels из колонки «Группа тем».
Каждое обращение уже принадлежит одной из 26 категорий — это полностью
self-supervised сигнал, ручная разметка не требуется.

Используется MultipleNegativesRankingLoss из sentence-transformers:
- anchor = обращение
- positive = другое обращение той же категории (group_name)
- negatives = остальные пары в батче

Модель обучается приближать обращения одной категории и отдалять разные.
Это улучшает кластеризацию (DBSCAN получит более чистые группы) и
классификацию LogReg (эмбеддинги станут более separable).

Запуск:
    python -m backend.scripts.finetune_bge --input data/input/<file>.xlsx --epochs 1 --out data/cache/bge-lora

Затем установить EMBEDDING_LORA_PATH=data/cache/bge-lora в .env и перезапустить.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import random
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


def build_training_pairs(
    texts: list[str],
    groups: list[str],
    *,
    min_per_group: int = 4,
    max_pairs_per_group: int = 400,
    seed: int = 42,
) -> list[tuple[str, str]]:
    """Соберём positive pairs: (anchor, positive) из одной группы."""
    rng = random.Random(seed)
    by_group: dict[str, list[str]] = defaultdict(list)
    for text, group in zip(texts, groups):
        if text and group:
            by_group[group].append(text)

    pairs: list[tuple[str, str]] = []
    for group, group_texts in by_group.items():
        if len(group_texts) < min_per_group:
            continue
        # Shuffle within group, take pairs of adjacent texts
        shuffled = group_texts.copy()
        rng.shuffle(shuffled)
        n_pairs = min(max_pairs_per_group, len(shuffled) // 2)
        for i in range(n_pairs):
            a = shuffled[2 * i]
            b = shuffled[2 * i + 1]
            if a != b:
                pairs.append((a, b))

    rng.shuffle(pairs)
    return pairs


def _merge_lora_safetensors(out_path: Path, *, lora_alpha: int, lora_r: int) -> bool:
    """Convert a PEFT-injected safetensors checkpoint into plain transformer weights.

    sentence-transformers saves PEFT-injected modules with keys like
    `dense.base_layer.weight` + `dense.lora_A/B.default.weight`. Plain
    SentenceTransformer loading expects `dense.weight`, so we merge the LoRA
    delta and write a regular checkpoint.
    """
    model_path = out_path / "model.safetensors"
    if not model_path.exists():
        logger.warning("Cannot merge LoRA tensors: %s does not exist", model_path)
        return False

    try:
        from safetensors.torch import load_file, save_file
    except ImportError as exc:
        raise RuntimeError("safetensors package is required to merge LoRA weights") from exc

    state = load_file(str(model_path))
    if not any(".base_layer." in key or ".lora_" in key for key in state):
        logger.info("Checkpoint %s already has plain transformer weights", model_path)
        return True

    scaling = float(lora_alpha) / float(lora_r)
    merged = {}
    merged_count = 0

    for key, tensor in state.items():
        if ".lora_A." in key or ".lora_B." in key:
            continue

        if key.endswith(".base_layer.weight"):
            prefix = key[: -len(".base_layer.weight")]
            out_key = f"{prefix}.weight"
            weight = tensor
            lora_a = state.get(f"{prefix}.lora_A.default.weight")
            lora_b = state.get(f"{prefix}.lora_B.default.weight")
            if lora_a is not None and lora_b is not None:
                delta = (lora_b.float() @ lora_a.float()) * scaling
                weight = (tensor.float() + delta).to(dtype=tensor.dtype)
                merged_count += 1
            merged[out_key] = weight
            continue

        if key.endswith(".base_layer.bias"):
            prefix = key[: -len(".base_layer.bias")]
            merged[f"{prefix}.bias"] = tensor
            continue

        merged[key] = tensor

    tmp_path = model_path.with_suffix(".merged.tmp")
    save_file(merged, str(tmp_path), metadata={"format": "pt"})
    del state
    del merged
    gc.collect()
    os.replace(tmp_path, model_path)
    logger.info("Merged %s LoRA layers into %s", merged_count, model_path)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Excel file with column 'Группа тем' (col 19) and 'Текст инцидента' (col 34)")
    parser.add_argument("--base-model", default=os.environ.get("EMBEDDING_MODEL_NAME", "BAAI/bge-m3"))
    parser.add_argument("--out", default="data/cache/bge-lora", help="Output directory for the LoRA adapter")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--lora-r", type=int, default=16, help="LoRA rank")
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--max-pairs-per-group", type=int, default=400)
    parser.add_argument("--max-seq", type=int, default=320)
    parser.add_argument("--no-lora", action="store_true", help="Full fine-tune instead of LoRA (warning: 568M params)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Lazy imports
    import torch
    from sentence_transformers import InputExample, SentenceTransformer, losses
    from torch.utils.data import DataLoader

    from backend.pipeline.loader import load_excel

    logger.info("Loading dataset from %s", args.input)
    df, stats = load_excel(args.input)
    logger.info("Loaded %s rows (raw=%s)", len(df), stats.get("raw_count"))

    if "group" not in df.columns:
        raise ValueError("Source file has no 'group' column; cannot build silver labels")

    texts = df["incident_text"].tolist()
    groups = df["group"].fillna("").tolist()

    pairs = build_training_pairs(
        texts,
        groups,
        max_pairs_per_group=args.max_pairs_per_group,
        seed=args.seed,
    )
    if len(pairs) < 100:
        raise ValueError(f"Not enough training pairs: {len(pairs)} (need at least 100). Likely too few per group.")
    logger.info("Built %s training pairs across %s groups", len(pairs), len(set(groups)))

    # Build SentenceTransformer
    logger.info("Loading base model %s", args.base_model)
    model = SentenceTransformer(args.base_model)
    model.max_seq_length = args.max_seq

    # Apply LoRA via PEFT (default path)
    if not args.no_lora:
        try:
            from peft import LoraConfig, get_peft_model, TaskType
        except ImportError as exc:
            raise RuntimeError("peft package is required for LoRA. pip install peft") from exc

        lora_config = LoraConfig(
            r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            bias="none",
            task_type=TaskType.FEATURE_EXTRACTION,
            target_modules=["query", "key", "value", "dense"],  # стандартные модули для XLM-R семьи
        )
        # SentenceTransformer хранит transformer в первой подмодели
        transformer_module = model._first_module().auto_model
        transformer_module = get_peft_model(transformer_module, lora_config)
        model._first_module().auto_model = transformer_module
        transformer_module.print_trainable_parameters()
        logger.info("LoRA applied (r=%s, alpha=%s)", args.lora_r, args.lora_alpha)

    # Data
    examples = [InputExample(texts=[a, b]) for a, b in pairs]
    dataloader = DataLoader(examples, shuffle=True, batch_size=args.batch_size)

    # MultipleNegativesRankingLoss использует in-batch negatives,
    # т.е. остальные пары в батче работают как негативы для текущей.
    train_loss = losses.MultipleNegativesRankingLoss(model)

    warmup_steps = int(len(dataloader) * args.epochs * 0.1)
    out_path = Path(args.out)
    out_path.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Training: epochs=%s, batch_size=%s, warmup_steps=%s, lr=%s, out=%s",
        args.epochs, args.batch_size, warmup_steps, args.lr, out_path,
    )

    model.fit(
        train_objectives=[(dataloader, train_loss)],
        epochs=args.epochs,
        warmup_steps=warmup_steps,
        optimizer_params={"lr": args.lr},
        output_path=str(out_path),
        show_progress_bar=True,
        use_amp=torch.cuda.is_available(),
    )

    lora_merged = False
    if not args.no_lora:
        transformer_module = model._first_module().auto_model
        if hasattr(transformer_module, "merge_and_unload"):
            logger.info("Merging LoRA adapter into base model for compatible SentenceTransformer save")
            model._first_module().auto_model = transformer_module.merge_and_unload()
            model.save(str(out_path))
            lora_merged = True
        else:
            logger.info("LoRA model has no merge_and_unload(); merging saved safetensors checkpoint")
            lora_merged = _merge_lora_safetensors(out_path, lora_alpha=args.lora_alpha, lora_r=args.lora_r)

    # Save metadata about training run
    meta = {
        "base_model": args.base_model,
        "training_pairs": len(pairs),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "lr": args.lr,
        "lora": not args.no_lora,
        "lora_r": args.lora_r if not args.no_lora else None,
        "lora_merged": lora_merged,
        "max_seq": args.max_seq,
    }
    with open(out_path / "training_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info("Done. Set EMBEDDING_LORA_PATH=%s and restart backend.", out_path)


if __name__ == "__main__":
    main()
