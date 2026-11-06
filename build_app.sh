#!/usr/bin/env bash
# Rebuilds the Windows app (needed after changing server.py, kaggle_ctl.py or app_main.py; web/, kaggle/ and prompts/ changes need no rebuild).
# Uses Wan Studio's build Python (.venv-app: Python 3.12 + PyInstaller + pywebview) by absolute path; it is only read, never changed.
cd "$(dirname "$0")"
W=$(cygpath -w "$(pwd)")
PY="$(cd ../Wan-Studio/.venv-app/Scripts && pwd)/python.exe"
PYTHONDONTWRITEBYTECODE=1 "$PY" -m PyInstaller --noconfirm --clean --windowed --name QwenImageStudio --icon "$W\web\qis.ico" --distpath app --workpath build/pyi --specpath build \
  --collect-all webview --collect-all kagglesdk --collect-all kaggle --hidden-import server --hidden-import kaggle_ctl --paths "$W" app_main.py > build_log.txt 2>&1
tail -1 build_log.txt
