# SuperBeavers/DigitalHack

Проект для хакатона от команды **SuperBeavers**. Это веб-сервис для анализа обращений граждан: он загружает Excel-файл, выделяет проблемные обращения, определяет тяжесть и категорию, строит кластеры проблем по муниципалитетам и показывает результаты в дашборде и чат-боте.

## Установка и запуск

1. Установите Python 3.11+, Node.js/npm, Docker Desktop и Ollama.
2. Скачайте модель для локального LLM:

```bash
ollama pull qwen3:4b
```

3. Установите зависимости backend:

```bash
pip install -r backend/requirements.txt
```

Для CPU-режима с ONNX INT8 подготовьте модель эмбеддингов:

```bash
python scripts/convert_to_onnx_int8.py
```

4. Установите зависимости frontend:

```bash
cd frontend
npm install
cd ..
```

5. Запустите проект:

```bash
start.bat
```

Для запуска CPU-конфигурации `ONNX INT8 + qwen3:4b` используйте:

```bash
start_cpu.bat
```

После запуска backend будет доступен по адресу `http://localhost:8001`, frontend — по адресу `http://localhost:5173`.

> Для Windows с NVIDIA GPU оставьте `EMBEDDING_DEVICE=cuda`.
>
> Для macOS используйте `EMBEDDING_DEVICE=mps`.
