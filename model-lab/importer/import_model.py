#!/usr/bin/env python3
"""Machine-readable adapter for the Model Lab Node.js server."""
from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path


def emit(payload: dict) -> None:
    stream = getattr(sys, "__stdout__", sys.stdout)
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def main() -> int:
    if len(sys.argv) != 3:
        emit({"type": "error", "message": "Ожидаются URL модели и каталог для импорта."})
        return 2

    url = sys.argv[1]
    output_root = Path(sys.argv[2]).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        from main import download_one

        def progress(message: str) -> None:
            emit({"type": "progress", "message": message})

        # The downloader's human-readable logs go to stderr. stdout stays JSONL.
        with redirect_stdout(io.StringIO()):
            result = download_one(url, out_root=output_root, progress=progress)

        model_dir = Path(result["dir"]).resolve()
        model_path = Path(result["path"]).resolve()
        relative_model = model_path.relative_to(model_dir).as_posix()
        emit({
            "type": "result",
            "name": result.get("name") or model_path.stem,
            "author": result.get("author") or "Unknown creator",
            "description": result.get("description") or "",
            "uid": result.get("uid"),
            "directory": str(model_dir),
            "model_file": relative_model,
            "file_name": model_path.name,
        })
        return 0
    except Exception as exc:
        emit({"type": "error", "message": str(exc) or type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
