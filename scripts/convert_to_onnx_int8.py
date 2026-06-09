"""Convert an embedding model to ONNX with INT8 dynamic quantization.

Usage:
    python scripts/convert_to_onnx_int8.py
    python scripts/convert_to_onnx_int8.py --model-name sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 --out-name multilingual-minilm-l12

Output:
    models/<out-name>-onnx/         — fp32 ONNX (intermediate)
    models/<out-name>-onnx-int8/    — quantized INT8 (use this in production)

On 8-core CPU with AVX-512 VNNI:
    sentence-transformers fp32: ~10 texts/sec → 400k = ~11 hours
    ONNX INT8:                  ~150-250 texts/sec → 400k ≈ 30-40 minutes
"""
import logging
import argparse
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def convert(model_name: str = "BAAI/bge-m3", out_name: str = "bge-m3", quantization: str = "avx2"):
    from optimum.onnxruntime import ORTModelForFeatureExtraction, ORTQuantizer
    from optimum.onnxruntime.configuration import AutoQuantizationConfig
    from transformers import AutoTokenizer

    project_root = Path(__file__).resolve().parents[1]
    onnx_dir = project_root / "models" / f"{out_name}-onnx"
    int8_dir = project_root / "models" / f"{out_name}-onnx-int8"

    onnx_dir.mkdir(parents=True, exist_ok=True)
    int8_dir.mkdir(parents=True, exist_ok=True)

    if not (onnx_dir / "model.onnx").exists():
        logger.info("Exporting %s to ONNX fp32 → %s", model_name, onnx_dir)
        model = ORTModelForFeatureExtraction.from_pretrained(model_name, export=True)
        model.save_pretrained(str(onnx_dir))
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        tokenizer.save_pretrained(str(onnx_dir))
        logger.info("ONNX fp32 export done")
    else:
        logger.info("Reusing existing ONNX fp32 at %s", onnx_dir)

    if (int8_dir / "model_quantized.onnx").exists():
        logger.info("INT8 model already exists at %s, skipping quantization", int8_dir)
        return int8_dir

    logger.info("Quantizing to INT8 (dynamic, %s) → %s", quantization, int8_dir)
    quantizer = ORTQuantizer.from_pretrained(str(onnx_dir))
    if quantization == "avx512_vnni":
        qconfig = AutoQuantizationConfig.avx512_vnni(is_static=False, per_channel=False)
    else:
        qconfig = AutoQuantizationConfig.avx2(is_static=False, per_channel=False)
    quantizer.quantize(save_dir=str(int8_dir), quantization_config=qconfig)

    # Copy tokenizer + config to int8 dir
    tokenizer = AutoTokenizer.from_pretrained(str(onnx_dir))
    tokenizer.save_pretrained(str(int8_dir))

    logger.info("INT8 quantization done: %s", int8_dir)
    return int8_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-name", default="BAAI/bge-m3")
    parser.add_argument("--out-name", default="bge-m3")
    parser.add_argument("--quantization", choices=["avx2", "avx512_vnni"], default="avx2")
    args = parser.parse_args()
    try:
        path = convert(args.model_name, args.out_name, args.quantization)
        print(f"\nOK — ONNX INT8 model ready: {path}")
        print("Set EMBEDDING_BACKEND=onnx in config.py or env to enable")
    except Exception as exc:
        logger.exception("Conversion failed: %s", exc)
        sys.exit(1)
