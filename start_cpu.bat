@echo off
REM Запуск backend в CPU-режиме (ONNX INT8 + qwen3:4b)
REM Используется для деплоя на Astra Linux или для тестирования CPU-стека

REM === Embeddings ===
set EMBEDDING_BACKEND=onnx
set EMBEDDING_DEVICE=cpu
set EMBEDDING_USE_FP16=false
set EMBEDDING_ONNX_DIR=models/bge-m3-onnx-int8
set EMBEDDING_ONNX_THREADS=8
set EMBEDDING_BATCH_SIZE=32
set EMBEDDING_MAX_SEQ_LENGTH=256
set EMBEDDING_LORA_PATH=

REM === LLM ===
set LLM_MODEL=qwen3:4b
set LLM_CONCURRENCY=3
set LLM_VERIFY_CONCURRENCY=2
set CLUSTER_NAME_CONCURRENCY=3

REM === Pipeline ===
set BOOTSTRAP_SAMPLE_SIZE=300
set CLUSTER_NAME_MAX_TOTAL=200
set LOW_CONFIDENCE_THRESHOLD=0.55

echo === CPU mode: ONNX INT8 + qwen3:4b ===
echo Embedding backend: %EMBEDDING_BACKEND%
echo LLM model:         %LLM_MODEL%
echo.

REM Проверка наличия ONNX INT8 модели
if not exist "models\bge-m3-onnx-int8\model_quantized.onnx" (
    if not exist "models\bge-m3-onnx-int8\model.onnx" (
        echo [!] ONNX модель не найдена в models\bge-m3-onnx-int8\
        echo [!] Запустите: python scripts/convert_to_onnx_int8.py
        echo.
        pause
        exit /b 1
    )
)

REM Проверка qwen3:4b в Ollama
ollama list 2>nul | findstr /C:"qwen3:4b" >nul
if errorlevel 1 (
    echo [!] qwen3:4b не найдена в Ollama. Скачиваю...
    ollama pull qwen3:4b
)

REM Запуск
python -m uvicorn backend.main:app --port 8001 --host 0.0.0.0
